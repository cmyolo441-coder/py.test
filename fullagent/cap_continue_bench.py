"""End-to-end benchmark: 25-call cap + "continue" without invalid_request_error.

Incident (2026-10-10): a turn hit the tool-call cap and the user said
"continue". The follow-up request re-sent the full capped-turn history
and the provider answered with ``[invalid_request_error] invalid request``.

What this module runs — real code paths, fake provider (no API key, no
network):

  1. A real ``Agent.run_turn`` against a fake ``chat_stream`` that
     returns 25 realistic tool calls (read_file / search_files /
     run_command, ~2KB results each, two consecutive failures so the
     retryhint path injects a mid-conversation system hint — exactly
     the incident shape). Tool execution is faked at the
     ``Agent._execute_tool`` seam with 6s reported durations, so the
     adaptive cap trips on the 25-call tier deterministically.
  2. Assert the cap fired: 25 tools executed, ``turn.error`` is a stop
     message that ``continueresume`` recognises as a capped stop.
  3. ``run_turn("continue")`` — the real
     ``continueresume.seed_continue_history`` hook reseeds the
     model-visible history to [system, original request, summary].
  4. The fake provider captures the exact continue request; a
     worker-12-style validator checks pairing (no orphaned tool_calls
     and no orphaned tool responses, either direction), roles (system
     only at position 0, tool messages only after their assistant
     tool_calls) and sizes (input + max_tokens inside the model
     window). Zero hard violations => the request would NOT produce
     invalid_request_error.
  5. Legacy contrast (sensitivity proof): the same pre-reseed history
     sent WITHOUT the reseed — the old behaviour — through the real
     ``_prune_for_model`` + ``build_payload`` path. It must show hard
     violations (orphaned tool response from the sliding-window cut),
     proving this test FAILS when the bug is present.
  6. Budgets: total simulated time < 120s; history sent on continue
     < 15% of the capped turn's history.

Run: ``python3 -m fullagent.cap_continue_bench`` — exits 0 with
``BENCH PASS`` when every assertion holds, 1 with ``BENCH FAIL`` and
the failed assertion names otherwise.

Nothing here touches ``_EMBEDDED_KEYS`` or any provider config.
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import tempfile
import threading
import time

# Keep the benchmark hermetic: event log / sessions / config go to a temp
# dir, never the user's real ~/.fullagent. Must be set before
# fullagent.config is imported (APP_DIR is computed at import time).
os.environ.setdefault("FULLAGENT_HOME",
                      tempfile.mkdtemp(prefix="cap-continue-bench-"))

from . import agent as _agent_mod
from . import continueresume as _continueresume
from .agent import prune_messages
from .client import (
    StreamResult,
    build_payload,
    effective_window,
    estimate_tokens,
)
from .config import Config

_BENCH_TIME_BUDGET_S = 120.0
_HISTORY_RATIO_MAX = 0.15
# Reported per-call duration: lands in the adaptive cap's "normal" tier
# (5s < avg < 15s) so the deterministic 25-call cap trips, not the
# fast-tier (40) or slow-tier (12) cap and not the time budget
# (25 * 6s = 150s < 180s budget).
_FAKE_CALL_DURATION_S = 6.0


# ---------------------------------------------------------------------------
# worker-12-style wire validation
# ---------------------------------------------------------------------------

_VALID_ROLES = {"system", "user", "assistant", "tool"}


def validate_wire_messages(messages, max_tokens, model):
    """Check a would-be provider request for invalid_request_error causes.

    Returns ``(hard_violations, soft_warnings)``. Hard violations are the
    shapes client.py's own history sanitizer treats as provider-400
    causes (orphaned tool_calls / tool responses, malformed calls,
    missing reasoning on tool-call messages, window overflow). Soft
    warnings are strict-provider hygiene (mid-conversation system
    messages, interleaving between a tool_call and its response).
    """
    hard: list[str] = []
    soft: list[str] = []
    if not messages:
        return ["empty message list"], soft
    msgs = [m for m in messages if isinstance(m, dict)]
    if len(msgs) != len(messages):
        hard.append("non-dict entry in message list")
    roles = [m.get("role") for m in msgs]

    # -- roles ----------------------------------------------------------
    if roles[0] != "system":
        hard.append(f"first message role is {roles[0]!r}, not 'system'")
    for i, r in enumerate(roles):
        if r not in _VALID_ROLES:
            hard.append(f"unknown role {r!r} at index {i}")
        elif r == "system" and i != 0:
            soft.append(f"mid-conversation system message at index {i}")
    if roles[-1] == "tool":
        hard.append("conversation ends on a tool message with no "
                    "following assistant turn")

    # -- pairing: every tool_call id <-> exactly one later tool response
    declared: dict[str, int] = {}   # tool_call id -> assistant msg index
    for i, m in enumerate(msgs):
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            if not isinstance(tc, dict):
                hard.append(f"non-dict tool_call at message index {i}")
                continue
            tid = tc.get("id")
            fn = tc.get("function") or {}
            if not tid or not isinstance(tid, str):
                hard.append(f"tool_call without string id at index {i}")
                continue
            if tid in declared:
                hard.append(f"duplicate tool_call id {tid!r}")
                continue
            declared[tid] = i
            if not (isinstance(fn, dict) and fn.get("name")):
                hard.append(f"tool_call {tid!r} has no function name")
        if m.get("tool_calls"):
            has_text = bool(m.get("content"))
            has_reason = (m.get("reasoning_content") is not None
                          or m.get("reasoning") is not None)
            if not has_text and not has_reason:
                hard.append(f"assistant tool_calls at index {i} carry "
                            "neither content nor reasoning_content")

    responded: dict[str, int] = {}  # tool_call id -> tool msg index
    for i, m in enumerate(msgs):
        if m.get("role") != "tool":
            continue
        tid = m.get("tool_call_id")
        if tid in responded:
            hard.append(f"duplicate tool response for {tid!r}")
            continue
        responded[tid] = i
        if tid not in declared:
            hard.append(f"orphaned tool response {tid!r} at index {i} — "
                        "no matching tool_call in history")
        elif responded[tid] < declared[tid]:
            hard.append(f"tool response {tid!r} precedes its tool_call")

    for tid, ai in declared.items():
        if tid not in responded:
            hard.append(f"orphaned tool_call {tid!r} (assistant index "
                        f"{ai}) — no matching tool response")

    # -- ordering: nothing may interleave between a tool_call block and
    #    its responses (strict providers reject the replay otherwise)
    open_ids: set[str] = set()
    for i, m in enumerate(msgs):
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            if open_ids:
                soft.append(f"assistant message at index {i} starts while "
                            f"tool_calls {sorted(open_ids)} await responses")
            open_ids = {tc.get("id") for tc in m["tool_calls"]
                        if isinstance(tc, dict) and tc.get("id")}
        elif role == "tool":
            open_ids.discard(m.get("tool_call_id"))
        elif role in ("user", "system") and open_ids:
            soft.append(f"{role} message at index {i} interleaved between "
                        f"tool_calls {sorted(open_ids)} and their responses")
            open_ids = set()

    # -- sizes: the request must fit the window --------------------------
    try:
        input_tokens = estimate_tokens(msgs, model.id)
    except Exception:  # noqa: BLE001 — estimation never fails the bench
        input_tokens = -1
    if input_tokens >= 0:
        window = effective_window(model)
        if input_tokens + max_tokens > window:
            hard.append(f"request over window: ~{input_tokens} input + "
                        f"{max_tokens} max_tokens > {window}")
    if max_tokens < 1024:
        hard.append(f"max_tokens {max_tokens} below minimum completion "
                    "floor — provider would 400")
    return hard, soft


def _history_chars(messages) -> int:
    """Char size of the turn history, excluding the shared system prompt."""
    n = 0
    for m in messages:
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, str):
            n += len(c)
        for tc in m.get("tool_calls") or []:
            fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
            n += len(str(fn.get("name", ""))) + len(str(fn.get("arguments", "")))
    return n


# ---------------------------------------------------------------------------
# fake provider + fake tool executor (no API key, no network)
# ---------------------------------------------------------------------------

_FILE_BODY = ("def handler(req):\n" + "    # " + "x" * 60 + "\n") * 28  # ~2.2KB

_TOOL_SEQUENCE = ["read_file", "search_files", "run_command", "read_file",
                  "edit_file"]


class FakeProvider:
    """Stands in for chat_stream at the exact seam Agent._complete uses.

    Returns 25 realistic single-tool-call turns, then a plain final
    answer. Records every request's messages so the bench can validate
    the exact continue payload the provider would receive.
    """

    def __init__(self, n_tool_turns=25):
        self.n_tool_turns = n_tool_turns
        self.calls = 0
        self.requests: list[list[dict]] = []
        self._lock = threading.Lock()

    def __call__(self, *args, **kwargs):
        messages = args[3] if len(args) > 3 else kwargs.get("messages", [])
        with self._lock:
            self.calls += 1
            n = self.calls
            self.requests.append(copy.deepcopy(messages))
        if n <= self.n_tool_turns:
            # calls 8 and 9 fail identically on the SAME tool so the
            # retryhint path injects its mid-conversation system hint
            # (part of the incident shape); every other call succeeds
            name = ("read_file" if n in (8, 9)
                    else _TOOL_SEQUENCE[n % len(_TOOL_SEQUENCE)])
            tc = {
                "id": f"call_{n:03d}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(
                        {"path": f"/repo/src/mod{n}.py", "q": "handler"}),
                },
            }
            return StreamResult(
                content=f"Step {n}: inspecting module {n}.",
                reasoning="continuing the refactor plan",
                tool_calls=[tc],
                usage={"input_tokens": 500, "output_tokens": 60},
            )
        return StreamResult(
            content="Done — refactor complete, all checks pass.",
            reasoning="",
            tool_calls=[],
            usage={"input_tokens": 500, "output_tokens": 20},
        )


def _fake_execute_tool(self, ev, approve, on_status, **kwargs):
    """Fake tool execution at the Agent._execute_tool seam.

    Realistic ~2KB results; calls 8 and 9 fail identically so the
    retryhint path injects its mid-conversation system hint (part of
    the incident shape). Reported durations hit the adaptive cap's
    normal tier so the deterministic 25-call cap trips.
    """
    m = re.search(r"mod(\d+)", json.dumps(ev.args))
    k = int(m.group(1)) if m else 0
    if k in (8, 9):
        ev.status = "error"
        ev.result = "ERROR: file not found: /repo/src/missing.py"
    else:
        ev.status = "done"
        ev.result = f"OK {ev.name} {ev.args.get('path', '')}\n{_FILE_BODY}"
    ev.duration = _FAKE_CALL_DURATION_S


# ---------------------------------------------------------------------------
# the benchmark
# ---------------------------------------------------------------------------

def run_bench():
    """Run the end-to-end cap/continue simulation. Returns a report dict."""
    t_start = time.time()
    report: dict = {
        "failures": [],
        "soft_warnings": [],
        "legacy_hard": [],
        "legacy_soft": [],
    }

    cfg = Config()
    agent = _agent_mod.Agent(cfg)
    fake = FakeProvider(n_tool_turns=25)

    # seam patches (restored afterwards): provider + tool executor
    real_chat_stream = _agent_mod.chat_stream
    real_execute = _agent_mod.Agent._execute_tool
    _agent_mod.chat_stream = fake
    _agent_mod.Agent._execute_tool = _fake_execute_tool

    # spy on the continue hook to capture the full pre-reseed history
    real_seed = _continueresume.seed_continue_history
    captured: dict = {}
    def _spy_seed(ag, user_text):
        captured["full"] = copy.deepcopy(ag.messages)
        reseeded = real_seed(ag, user_text)
        captured["reseeded"] = reseeded
        captured["after"] = copy.deepcopy(ag.messages)
        return reseeded
    _continueresume.seed_continue_history = _spy_seed

    noop = lambda *a, **k: None  # noqa: E731
    approve = lambda tool, args: True  # noqa: E731

    try:
        # -- turn 1: the incident turn --------------------------------
        turn1 = agent.run_turn(
            "Refactor the request handler module end to end",
            on_token=noop, on_reasoning=noop,
            on_tool_call=noop, on_tool_update=noop,
            on_status=noop, approve=approve)
        report["turn1_tools"] = len(turn1.tools)
        report["turn1_error"] = turn1.error
        report["provider_calls_after_turn1"] = fake.calls

        if len(turn1.tools) != 25:
            report["failures"].append(
                f"cap did not fire at 25 calls: {len(turn1.tools)} "
                "tools executed")
        if not _continueresume._is_capped_stop(turn1.error or ""):
            report["failures"].append(
                f"turn1 error not recognised as a capped stop: "
                f"{turn1.error!r}")
        if "continue" not in (turn1.error or "").lower():
            report["failures"].append(
                "turn1 error does not invite 'continue'")

        # -- turn 2: the user says "continue" --------------------------
        turn2 = agent.run_turn(
            "continue",
            on_token=noop, on_reasoning=noop,
            on_tool_call=noop, on_tool_update=noop,
            on_status=noop, approve=approve)
        report["turn2_error"] = turn2.error
        report["turn2_tools"] = len(turn2.tools)
        report["reseeded"] = bool(captured.get("reseeded"))
        report["provider_calls_total"] = fake.calls

        if not captured.get("reseeded"):
            report["failures"].append(
                "seed_continue_history did not reseed on 'continue'")
        if turn2.error:
            report["failures"].append(
                f"continue turn errored: {turn2.error!r}")
        if fake.calls != 26:
            report["failures"].append(
                f"expected 26 provider calls (25 + continue), "
                f"saw {fake.calls}")

        # -- validate the exact continue request the provider got ------
        continue_request = fake.requests[-1]
        report["continue_request_messages"] = len(continue_request)
        report["continue_request_roles"] = [
            m.get("role") for m in continue_request
            if isinstance(m, dict)]
        schemas = agent._tool_schemas()
        payload = build_payload(agent.model, agent.effort,
                                copy.deepcopy(continue_request), schemas)
        hard, soft = validate_wire_messages(
            payload["messages"], payload.get("max_tokens", 0), agent.model)
        report["continue_hard"] = hard
        report["soft_warnings"] = soft
        if hard:
            report["failures"].append(
                f"continue request INVALID ({len(hard)} hard "
                f"violations): {hard[0]}")

        # -- legacy contrast: old behaviour must be invalid ------------
        # (a) the raw captured history, re-sent without the reseed —
        #     informational: incidental hint messages make the exact
        #     window cut nondeterministic, so this is reported, not
        #     asserted.
        full_history = captured.get("full", [])
        legacy_raw = list(full_history) + [{"role": "user",
                                            "content": "continue"}]
        legacy_raw_pruned = prune_messages(legacy_raw,
                                           window=cfg.prune_window)
        legacy_raw_payload = build_payload(
            agent.model, agent.effort,
            copy.deepcopy(legacy_raw_pruned), schemas)
        raw_hard, raw_soft = validate_wire_messages(
            legacy_raw_payload["messages"],
            legacy_raw_payload.get("max_tokens", 0), agent.model)
        report["legacy_raw_hard"] = raw_hard
        report["legacy_raw_soft"] = raw_soft

        # (b) deterministic sensitivity probe: rebuild the canonical
        #     [system, user, (assistant, tool) x 25] history from the
        #     REAL captured messages (system prompt, original user
        #     message, and the 25 real assistant/tool pairs matched by
        #     tool_call id — incidental mid-turn hint messages excluded)
        #     + "continue". At 53 messages the real 40-window sliding
        #     cut provably lands mid-pair (the tail starts with
        #     tool_006's response whose assistant message was cut) —
        #     the exact shape that makes providers answer
        #     invalid_request_error. The old code sent this; the
        #     validator must flag it, or this bench could not catch
        #     the bug.
        def _canon():
            msgs = [m for m in full_history if isinstance(m, dict)]
            if not msgs or msgs[0].get("role") != "system":
                return None
            users = [m for m in msgs if m.get("role") == "user"]
            if not users:
                return None
            assts = {}
            tools = {}
            for m in msgs:
                if m.get("role") == "assistant":
                    for tc in m.get("tool_calls") or []:
                        if isinstance(tc, dict) and tc.get("id"):
                            assts[tc["id"]] = m
                elif m.get("role") == "tool" and m.get("tool_call_id"):
                    tools[m["tool_call_id"]] = m
            pairs = []
            for n in range(1, 26):
                tid = f"call_{n:03d}"
                if tid not in assts or tid not in tools:
                    return None
                pairs.append(assts[tid])
                pairs.append(tools[tid])
            return [msgs[0], users[0]] + pairs

        canonical = _canon()
        report["legacy_canonical_messages"] = len(canonical) \
            if canonical else 0
        probe_ok = canonical is not None and len(canonical) == 52 \
            and canonical[-1].get("role") == "tool"
        if not probe_ok:
            report["failures"].append(
                "sensitivity probe setup failed: could not rebuild "
                "the canonical 25-pair history from the captured "
                "turn")
            legacy_hard, legacy_soft = [], []
        else:
            legacy = canonical + [{"role": "user", "content": "continue"}]
            legacy_pruned = prune_messages(legacy, window=cfg.prune_window)
            legacy_payload = build_payload(
                agent.model, agent.effort,
                copy.deepcopy(legacy_pruned), schemas)
            legacy_hard, legacy_soft = validate_wire_messages(
                legacy_payload["messages"],
                legacy_payload.get("max_tokens", 0), agent.model)
        report["legacy_hard"] = legacy_hard
        report["legacy_soft"] = legacy_soft
        report["legacy_pruned_messages"] = len(legacy_pruned) \
            if probe_ok else 0
        if probe_ok and not legacy_hard:
            report["failures"].append(
                "sensitivity check failed: the UNRESEEDED (legacy) "
                "continue request validates clean — this test would "
                "not catch the bug")

        # -- history-size ratio -----------------------------------------
        full_chars = _history_chars(full_history[1:])      # excl. system
        cont_chars = _history_chars(continue_request[1:])  # excl. system
        report["full_history_chars"] = full_chars
        report["continue_history_chars"] = cont_chars
        ratio = (cont_chars / full_chars) if full_chars else 1.0
        report["history_ratio"] = round(ratio, 4)
        if ratio >= _HISTORY_RATIO_MAX:
            report["failures"].append(
                f"continue history {cont_chars:,} chars is "
                f"{ratio:.1%} of the capped turn's {full_chars:,} "
                f"chars (budget {_HISTORY_RATIO_MAX:.0%})")

        # -- summary present in the reseeded history --------------------
        after = captured.get("after", [])
        summary_msgs = [m for m in after
                        if isinstance(m, dict)
                        and m.get("role") == "assistant"]
        report["summary_chars"] = sum(
            len(m.get("content", "")) for m in summary_msgs
            if isinstance(m.get("content"), str))
        if not summary_msgs or not any(
                isinstance(m.get("content"), str) and m["content"].strip()
                for m in summary_msgs):
            report["failures"].append(
                "reseeded history has no assistant summary message")
    finally:
        _agent_mod.chat_stream = real_chat_stream
        _agent_mod.Agent._execute_tool = real_execute
        _continueresume.seed_continue_history = real_seed

    report["elapsed_s"] = round(time.time() - t_start, 2)
    if report["elapsed_s"] >= _BENCH_TIME_BUDGET_S:
        report["failures"].append(
            f"bench took {report['elapsed_s']}s "
            f"(budget {_BENCH_TIME_BUDGET_S:.0f}s)")
    report["passed"] = not report["failures"]
    return report


# ---------------------------------------------------------------------------
# self-test entry point (repo convention: PASS/FAIL lines for run_selftests)
# ---------------------------------------------------------------------------

def _check(failures, name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name
          + (f" [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


if __name__ == "__main__":
    failures: list[str] = []
    rep = run_bench()

    _check(failures, "25 tool calls executed before the cap",
           rep.get("turn1_tools") == 25, f"got {rep.get('turn1_tools')}")
    _check(failures, "cap stop recognised by continueresume",
           _continueresume._is_capped_stop(rep.get("turn1_error") or ""))
    _check(failures, "'continue' reseeded the history",
           rep.get("reseeded") is True)
    _check(failures, "continue request has zero hard violations",
           not rep.get("continue_hard"), str(rep.get("continue_hard")))
    _check(failures, "legacy (unreseeded) request shows the bug",
           bool(rep.get("legacy_hard")),
           "no violations found on the old path")
    _check(failures, "continue history < 15% of capped turn history",
           rep.get("history_ratio", 1.0) < _HISTORY_RATIO_MAX,
           f"ratio={rep.get('history_ratio')}")
    _check(failures, "continue turn completed without error",
           not rep.get("turn2_error"), str(rep.get("turn2_error")))
    _check(failures, "total simulated time < 120s",
           rep.get("elapsed_s", 1e9) < _BENCH_TIME_BUDGET_S,
           f"{rep.get('elapsed_s')}s")

    print()
    print(f"history: {rep.get('continue_history_chars'):,} chars on "
          f"continue vs {rep.get('full_history_chars'):,} chars capped "
          f"turn ({rep.get('history_ratio', 0):.1%})")
    print(f"continue request roles: {rep.get('continue_request_roles')}")
    print(f"legacy hard violations (bug proof): {rep.get('legacy_hard')}")
    if rep.get("legacy_raw_hard") or rep.get("legacy_raw_soft"):
        print(f"raw legacy history also showed: "
              f"{rep.get('legacy_raw_hard')} {rep.get('legacy_raw_soft')}")
    if rep.get("soft_warnings"):
        print(f"soft warnings on continue request: "
              f"{rep['soft_warnings']}")
    print(f"elapsed: {rep.get('elapsed_s')}s")
    print()
    if failures:
        print(f"BENCH FAIL ({len(failures)}): {', '.join(failures)}")
        sys.exit(1)
    print("BENCH PASS — 25-call cap + continue is valid end to end")
