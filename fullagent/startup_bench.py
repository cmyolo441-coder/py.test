"""Startup-time benchmark + self-test (Worker 8/20).

Proves the cold-start budget: ``import fullagent.agent`` + ``Agent(Config())``
must complete in < 2.0s, and expensive work (subsystem imports, subsystem
construction, feature-module registration, env probing) must stay deferred
until first use.

Run:  python3 -m fullagent.startup_bench
Exit 0 + "STARTUP BENCH PASS" when every gate holds.

What it measures (each in a FRESH interpreter, so file-cache coldness is
visible across runs):
  * cold `import fullagent.agent` time
  * cold import + `Agent(Config())` construction time  (the <2s gate)
  * laziness: heavy subsystem / feature modules must NOT be in
    sys.modules right after construction
  * deferred session: `_session_started` False at construct, tools grow
    from core-only to full after the first turn
  * first-turn latency (informational only — real work happens there)

Gates: FAIL (exit 1) if import+construct >= 2.0s or any laziness /
deferral assertion breaks.
"""

from __future__ import annotations

import subprocess
import sys
import time

REPO = __file__.rsplit("/fullagent/", 1)[0]
BUDGET_S = 2.0

# Subsystem / feature modules that must NOT be imported merely by
# importing fullagent.agent and constructing an Agent. (fullagent.tools
# is expected — build_registry() runs at construction.)
HEAVY_DENYLIST = [
    "fullagent.crew", "fullagent.judge", "fullagent.debate",
    "fullagent.workflows", "fullagent.daemon", "fullagent.council",
    "fullagent.nexus", "fullagent.oracle", "fullagent.forge",
    "fullagent.brain", "fullagent.goal", "fullagent.memory",
    # feature modules (registered on first turn, never at construction)
    "fullagent.dockerops", "fullagent.browserauto", "fullagent.doctor",
    "fullagent.tui",
]


def _py(code: str) -> str:
    r = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        cwd=REPO, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"probe failed:\n{r.stdout}\n{r.stderr}")
    return r.stdout.strip()


def bench_import() -> float:
    out = _py(
        "import time; t0=time.perf_counter();"
        "from fullagent.agent import Agent; t1=time.perf_counter();"
        "print(f'{t1-t0:.3f}')")
    return float(out)


def bench_construct() -> tuple[float, list[str], bool, int]:
    out = _py(
        "import time, sys;"
        "t0=time.perf_counter();"
        "from fullagent.agent import Agent;"
        "from fullagent.config import Config;"
        "a = Agent(Config());"
        "t1=time.perf_counter();"
        "leaked = [m for m in " + repr(HEAVY_DENYLIST) + " if m in sys.modules];"
        "print(f'{t1-t0:.3f}');"
        "print('LEAKED:' + ','.join(leaked));"
        "print('DEFERRED:' + str(not a._session_started));"
        "print('TOOLS:' + str(len(a.tools)))")
    secs_s, leaked_s, deferred_s, tools_s = out.splitlines()
    leaked = leaked_s[len("LEAKED:"):].split(",") if leaked_s != "LEAKED:" else []
    return (float(secs_s), leaked,
            deferred_s == "DEFERRED:True", int(tools_s[len("TOOLS:"):]))



def bench_first_turn() -> tuple[float, int]:
    out = _py(
        "import time;"
        "from types import SimpleNamespace;"
        "from fullagent.agent import Agent;"
        "from fullagent.config import Config;"
        "a = Agent(Config());"
        "a._complete = lambda *x, **k: SimpleNamespace(content='done',"
        " reasoning='', tool_calls=[], usage=None);"
        "a._execute_tool = lambda *x, **k: 'ok';"
        "t0=time.perf_counter();"
        "a.run_turn('hi', on_token=lambda t: None, on_reasoning=lambda r: None,"
        " on_tool_call=lambda e: None, on_tool_update=lambda e: None,"
        " on_status=lambda s: None, approve=lambda t, d: True);"
        "t1=time.perf_counter();"
        "print(f'{t1-t0:.3f}');"
        "print('STARTED:' + str(a._session_started));"
        "print('TOOLS:' + str(len(a.tools)));"
        "print('HAS_TODO:' + str('TodoWrite' in a.tools))")
    secs_s, started_s, tools_s, todo_s = out.splitlines()
    assert started_s == "STARTED:True", "session did not start on first turn"
    assert todo_s == "HAS_TODO:True", "feature tools missing after first turn"
    return float(secs_s), int(tools_s[len("TOOLS:"):])


def main() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        print(("PASS " if cond else "FAIL ") + name +
              (f" ({detail})" if detail else ""))
        if not cond:
            failures.append(name)

    t_import = bench_import()
    print(f"cold import fullagent.agent: {t_import:.2f}s")
    check("import < 2.0s", t_import < BUDGET_S, f"{t_import:.2f}s")

    t_construct, leaked, deferred, n_core_tools = bench_construct()
    print(f"cold import + Agent(): {t_construct:.2f}s")
    check("import+construct < 2.0s", t_construct < BUDGET_S,
          f"{t_construct:.2f}s")
    check("no heavy modules at construct", not leaked,
          f"leaked={leaked}" if leaked else "all deferred")
    check("session start deferred", deferred)
    check("core tools present at construct", n_core_tools >= 10,
          f"{n_core_tools} tools")

    t_turn, n_full_tools = bench_first_turn()
    print(f"first run_turn (deferred work lands here): {t_turn:.2f}s")
    check("first turn completes", True, f"{t_turn:.2f}s")
    check("feature tools registered on first turn",
          n_full_tools > n_core_tools,
          f"{n_core_tools} -> {n_full_tools} tools")

    print()
    if failures:
        print(f"STARTUP BENCH FAIL: {failures}")
        return 1
    print("STARTUP BENCH PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
