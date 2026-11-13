"""Per-turn exact-duplicate tool-call deduplication.

Incident (2026-10-09, the "512s turn"): the model issued the IDENTICAL
failing MultiEdit call over and over. Each duplicate burned a full model
round-trip (~2.5s) plus tool execution time, and the identical retry loop
dominated the turn.

This module keeps a tiny per-turn cache on the Agent instance, keyed on
(tool_name, sha256(canonicalized args JSON)):

    check(agent, tool_name, args) -> (result, status) | None
        Returns the cached result WITH the dedup note appended on a hit,
        None on a miss. Results are deep-copied both ways so caching never
        mutates, and never shares, a live result object.

    store(agent, tool_name, args, result, status="done") -> None
        Records a completed execution (stores a deep copy).

    reset(agent) -> None
        Clears the turn cache. Called once at each turn start
        (Agent.run_turn), so dedup is turn-scoped only.

The hook in Agent._execute_tool consults check() before executing and
store() after; both are marked `# tooldedup hook` there.

Only "done"/"error" results are cached by the hook — "blocked"/"denied"
results are NOT cached, so permission gates, plan-mode blocks, and user
denials are re-evaluated every time.
"""

from __future__ import annotations

import copy
import hashlib
import json

DEDUP_NOTE = ("[dedup] identical call already ran this turn — "
              "returning cached result instead of re-executing.")

_CACHE_ATTR = "_tooldedup_cache"

# Bound the cache: a turn with thousands of tool calls stays small.
# Oldest entries are evicted first (dicts preserve insertion order).
_MAX_ENTRIES = 4096


def _cache(agent) -> dict:
    """Return (creating if needed) the agent's per-turn dedup cache."""
    c = getattr(agent, _CACHE_ATTR, None)
    if c is None:
        c = {}
        setattr(agent, _CACHE_ATTR, c)
    return c


def key(tool_name: str, args: dict) -> tuple:
    """Cache key: (tool_name, sha256 of canonicalized args JSON).

    Canonicalization: sorted keys, compact separators, so identical
    logical args hash identically regardless of dict insertion order.
    Non-JSON-serializable values fall back to str() (deterministic
    within a process, which is all a per-turn cache needs).
    """
    try:
        canon = json.dumps(args, sort_keys=True, separators=(",", ":"),
                           default=str)
    except (TypeError, ValueError):
        # Extremely defensive: args that even str() can't handle.
        canon = repr(sorted(args.items(), key=lambda kv: str(kv[0])))
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    return (tool_name, digest)


# Tools whose result depends only on workspace state that THIS agent's
# mutating tools change. Their results may be replayed until something
# mutates state (see store(): any other tool invalidates the cache).
_PURE_READ_TOOLS = frozenset({
    "read_file", "Read", "list_dir", "file_info", "search_files",
    "glob_files", "NotebookRead", "DbSchema", "DbTables", "GitDiff",
    "GitLog", "GitStatus", "code_symbols", "TodoRead", "ImageRead",
})

# File-edit tools: a FAILED call changed nothing, so an identical retry
# fails identically until something else changes state (the 512s
# incident). A SUCCESSFUL call mutated state: never replay it, and drop
# every cached read.
_EDIT_TOOLS = frozenset({
    "edit_file", "MultiEdit", "multiedit", "write_file", "Write",
    "apply_patch", "NotebookEdit", "NotebookInsert", "NotebookDelete",
})


def _cacheable(tool_name, status) -> bool:
    if tool_name in _PURE_READ_TOOLS:
        return status in ("done", "error")
    if tool_name in _EDIT_TOOLS:
        return status == "error"
    # Everything else (shell commands, BashOutput/TaskOutput polling,
    # web fetches, git commit/push, DB writes, unknown plugins...) is
    # time-varying or side-effecting: replaying a cached result returns
    # stale data -- e.g. "pytest" re-run after a fix reported the OLD
    # failure, and a BashOutput poll never saw new output, so the agent
    # looped forever waiting.
    return False


def check(agent, tool_name, args):
    """Return (result_with_note, status) if this exact call already ran
    this turn, else None. Never mutates the cached entry."""
    c = getattr(agent, _CACHE_ATTR, None)
    if not c:
        return None
    if tool_name not in _PURE_READ_TOOLS and tool_name not in _EDIT_TOOLS:
        return None
    hit = c.get(key(tool_name, args))
    if hit is None:
        return None
    result, status = hit
    return (f"{copy.deepcopy(result)}\n{DEDUP_NOTE}", status)


def store(agent, tool_name, args, result, status="done"):
    """Store a completed execution result (deep-copied, so later
    mutation of the caller's result object can't corrupt the cache)."""
    c = _cache(agent)
    if not _cacheable(tool_name, status):
        if tool_name not in _PURE_READ_TOOLS:
            # Possibly state-changing: every cached read/failed edit may
            # now be stale.
            c.clear()
        return
    if len(c) >= _MAX_ENTRIES:
        c.pop(next(iter(c)))  # evict oldest
    c[key(tool_name, args)] = (copy.deepcopy(result), status)


def reset(agent):
    """Clear the per-turn cache. Called at each turn start."""
    try:
        setattr(agent, _CACHE_ATTR, {})
    except Exception:  # noqa: BLE001 — a failed reset must never kill a turn
        pass


if __name__ == "__main__":
    # Self-test: exercise check/store/reset without the agent.
    class _FakeAgent:
        pass

    a = _FakeAgent()

    # 1. Miss on a fresh agent (no cache yet).
    assert check(a, "MultiEdit",
                 {"edits": [{"path": "x.py", "old": "a", "new": "b"}]}) is None

    # 2. Store, then an identical call is a hit carrying the note.
    args1 = {"edits": [{"path": "x.py", "old": "a", "new": "b"}]}
    store(a, "MultiEdit", args1, "applied 3 edits", "error")
    hit = check(a, "MultiEdit",
                {"edits": [{"path": "x.py", "old": "a", "new": "b"}]})
    assert hit is not None, "expected a dedup hit"
    assert DEDUP_NOTE in hit[0], "hit must carry the dedup note"
    assert hit[1] == "error"
    assert "applied 3 edits" in hit[0]

    # 3. Canonicalization: different dict key ORDER is still the same call.
    hit_reordered = check(a, "MultiEdit",
                          {"edits": [{"new": "b", "path": "x.py",
                                      "old": "a"}]})
    assert hit_reordered is not None, "key order must not matter"

    # 4. Miss: different args.
    assert check(a, "MultiEdit",
                 {"edits": [{"path": "y.py", "old": "a",
                             "new": "b"}]}) is None

    # 5. Miss: same args, different tool name.
    assert check(a, "edit_file",
                 {"edits": [{"path": "x.py", "old": "a",
                             "new": "b"}]}) is None

    # 6. Failure results cache too (the 512s incident was a failing call).
    store(a, "MultiEdit", {"path": "z.py"}, "ERROR: no match", "error")
    fail_hit = check(a, "MultiEdit", {"path": "z.py"})
    assert fail_hit is not None and fail_hit[1] == "error"
    assert "ERROR: no match" in fail_hit[0]
    assert DEDUP_NOTE in fail_hit[0]

    # 7. store() copies: mutating the original afterwards, or mutating a
    #    returned hit, must not corrupt the cached entry.
    orig = {"edits": [{"path": "w.py"}]}
    store(a, "MultiEdit", orig, ["r1"], "error")
    orig["edits"].append({"path": "MUTATED"})
    r1 = check(a, "MultiEdit", {"edits": [{"path": "w.py"}]})
    assert r1 is not None and r1[0] == "['r1']\n" + DEDUP_NOTE, r1
    r2 = check(a, "MultiEdit", {"edits": [{"path": "w.py"}]})
    assert r2[0] == r1[0], "second hit must equal the first"

    # 7b. Side-effecting / polling tools are never replayed, and they
    #     invalidate cached reads (state may have changed).
    store(a, "run_command", {"command": "pytest"}, "1 failed", "done")
    assert check(a, "run_command", {"command": "pytest"}) is None
    store(a, "read_file", {"path": "f"}, "old", "done")
    assert check(a, "read_file", {"path": "f"}) is not None
    store(a, "edit_file", {"path": "f"}, "Updated f", "done")
    assert check(a, "read_file", {"path": "f"}) is None
    assert check(a, "edit_file", {"path": "f"}) is None

    # 8. reset() clears everything -> miss again.
    reset(a)
    assert check(a, "MultiEdit",
                 {"edits": [{"path": "x.py", "old": "a",
                             "new": "b"}]}) is None

    print("tooldedup self-tests: all 8 assertions passed")
