"""``/swarm demo`` — a live, offline swarm for trying the dashboard.

Runs a REAL ``ParallelSubagents`` crew (real thread pool, real DAG waves,
real read-only tools against the current workspace, real telemetry) whose
only simulated part is the model: a local stand-in that takes a few
hundred milliseconds per step, reads a few real files and then reports.
No network, no API key, no tokens billed — the token counts are local
estimates and the dashboard header says DEMO.

The demo crew is separate from the agent's own crew, so its reports never
reach the agent and never wake a follow-up turn.
"""
from __future__ import annotations

import json
import random
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from .subagents import PARALLEL_SUBAGENT_READ_TOOLS, ParallelSubagents

DEMO_GOAL = "DEMO · simulated model, real swarm engine (no API calls)"

_PLAN = (
    ("security", "auth-audit", "Audit authentication and session handling"),
    ("reviewer", "api-review", "Review the public API surface for breakage"),
    ("optimizer", "perf-scan", "Find hot paths and needless allocations"),
    ("tester", "test-gaps", "Map untested branches in the core modules"),
    ("architect", "layering", "Check module layering and import cycles"),
    ("debugger", "error-paths", "Trace error handling on failure paths"),
    ("documenter", "docs-sync", "Compare README claims with the code"),
    ("analyst", "synthesis", "Merge every finding into one ranked report"),
)


class _NullLog:
    def append(self, *_a, **_k):
        return SimpleNamespace(id=uuid.uuid4().hex[:8])


class SimulatedModel:
    """Offline stand-in for the provider: real latency, real tool calls."""

    def __init__(self, files: list[str], *, seed: int | None = None,
                 speed: float = 1.0) -> None:
        self.files = files or ["README.md"]
        self.rng = random.Random(seed)
        self.speed = max(0.05, float(speed))

    def _sleep(self, seconds: float, should_cancel: Callable[[], bool] | None):
        deadline = time.monotonic() + seconds / self.speed
        while time.monotonic() < deadline:
            if should_cancel is not None and should_cancel():
                return
            time.sleep(0.02)

    def __call__(self, _provider, _model, _effort, messages, _schemas,
                 timeout=None, should_cancel=None, **_kw) -> Any:
        steps_done = sum(1 for m in messages if m.get("role") == "assistant")
        rng = self.rng
        budget = 3 + (hash(messages[1].get("content", "")) % 4)
        self._sleep(rng.uniform(0.5, 1.6), should_cancel)
        prompt_tokens = max(50, len(json.dumps(messages, default=str)) // 4)
        usage = {"prompt_tokens": prompt_tokens,
                 "completion_tokens": rng.randint(60, 320)}
        if steps_done < budget:
            calls = []
            for _ in range(rng.choice((1, 1, 2, 3))):
                path = rng.choice(self.files)
                kind = rng.random()
                if kind < 0.6:
                    name, args = "read_file", {"path": path}
                elif kind < 0.85:
                    name, args = "search_files", {
                        "pattern": rng.choice(("def ", "class ", "TODO",
                                               "raise ", "import ")),
                        "path": str(Path(path).parent)}
                else:
                    name, args = "list_dir", {"path": str(Path(path).parent)}
                calls.append({"id": f"call_{uuid.uuid4().hex[:10]}",
                              "type": "function",
                              "function": {"name": name,
                                           "arguments": json.dumps(args)}})
            return SimpleNamespace(content="", tool_calls=calls, usage=usage,
                                   reasoning="")
        task = ""
        for m in messages:
            if m.get("role") == "user":
                task = str(m.get("content", "")).split("TASK:", 1)[-1]
                break
        files = sorted({json.loads(c["function"]["arguments"]).get("path", "")
                        for m in messages if m.get("role") == "assistant"
                        for c in (m.get("tool_calls") or [])})
        summary = (f"[demo] {task.strip()[:90]} — inspected "
                   f"{len(files)} location(s); {rng.randint(1, 5)} "
                   "finding(s) noted (simulated model).")
        return SimpleNamespace(content=summary, tool_calls=[], usage=usage,
                               reasoning="")


def _workspace_files(root: Path, limit: int = 60) -> list[str]:
    out: list[str] = []
    for pattern in ("*.py", "*/*.py", "*.md"):
        for p in sorted(root.glob(pattern)):
            if p.is_file() and ".git" not in p.parts:
                out.append(str(p.relative_to(root)))
            if len(out) >= limit:
                return out
    return out


def start_demo(tools: dict, *, workers: int = 8,
               workspace: str | Path | None = None,
               speed: float = 1.0, seed: int | None = None,
               ) -> ParallelSubagents:
    """Launch the demo crew and return its manager (caller shuts it down)."""
    root = Path(workspace or Path.cwd()).expanduser().resolve()
    read_tools = {name: tool for name, tool in (tools or {}).items()
                  if name in PARALLEL_SUBAGENT_READ_TOOLS}
    model = SimpleNamespace(id="demo-sim", supports_tools=True)
    manager = ParallelSubagents(
        _NullLog(), read_tools,
        lambda _mid="": (SimpleNamespace(id="demo"), model,
                         SimpleNamespace(key="demo")),
        workspace=root, chat=SimulatedModel(_workspace_files(root),
                                            seed=seed, speed=speed))
    n = max(1, min(len(_PLAN), int(workers)))
    plan = list(_PLAN[:n])
    tasks = []
    for idx, (role, name, task) in enumerate(plan):
        item = {"task": task, "role": role, "name": name}
        if idx == n - 1 and n >= 3:
            # the last specialist is a second wave that waits for the rest
            item["depends_on"] = [p[1] for p in plan[:-1]]
        tasks.append(item)
    manager.spawn_many(tasks, goal=DEMO_GOAL)
    return manager
