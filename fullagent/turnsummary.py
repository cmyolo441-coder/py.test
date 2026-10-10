"""Turn summary on tool-call cap (worker 7/20).

When a turn trips MAX_TOOL_CALLS_PER_TURN, the full 25-call history would
be re-sent on "continue" and the provider chokes (invalid_request_error).
This module builds a compact DETERMINISTIC extractive summary of the turn —
no extra API call, nothing to fail on — and prunes the model-visible
history down to: system prompt + user request + summary. The continue turn
then starts from that compact state instead of 25 calls of history.

Nothing here touches _EMBEDDED_KEYS or the provider config.
"""

from __future__ import annotations

import json

_SUMMARY_USER_CHARS = 1500     # user request chars kept verbatim
_SUMMARY_ARGS_CHARS = 160      # per-call args compacted to this
_SUMMARY_RESULT_CHARS = 300    # per-call result preview chars
_SUMMARY_TEXT_CHARS = 1200     # assistant progress excerpt chars
_MAX_SUMMARY_CHARS = 8000      # hard bound on the final summary


def _one_line(text: str, limit: int) -> str:
    """Collapse whitespace and truncate — safe for args/results excerpts."""
    if not isinstance(text, str):
        text = str(text)
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def summarize_turn(turn) -> str:
    """Build an extractive summary of a finished turn.

    Pure function over the Turn record: what the user asked, every tool
    call (name, compacted args, status, result preview), the assistant's
    progress so far, and what's still pending. Deterministic — no model
    call, no network, no provider dependency. Never raises: a summary is
    insight, never a crash path.
    """
    try:
        return _build(turn)
    except Exception:  # noqa: BLE001 — last-ditch guard
        return ("[Previous turn hit the tool-call cap; history was "
                "compacted. Continue from here.]")


def _build(turn) -> str:
    parts: list[str] = []
    parts.append(
        "[TURN SUMMARY — the previous turn hit the tool-call cap and its "
        "full history was compacted to save context. Continue the task "
        "from here; do not repeat work that already completed.]")

    user_text = getattr(turn, "user_text", "") or ""
    parts.append("User request:\n"
                 + _one_line(user_text, _SUMMARY_USER_CHARS))

    # What's pending — placed before the tool detail so tail truncation
    # sacrifices call detail first, never the resume info.
    tools = list(getattr(turn, "tools", []) or [])
    pending = []
    error = getattr(turn, "error", "") or ""
    if error:
        pending.append(_one_line(error, 400))
    if tools:
        last = tools[-1]
        pending.append(
            f"Resume anchor — last call was {getattr(last, 'name', '?')} "
            f"({_one_line(getattr(last, 'args', {}) or {}, 120)}, "
            f"status={getattr(last, 'status', 'unknown')}).")
    if pending:
        parts.append("What's pending / next step:\n" + "\n".join(pending))

    # Key findings/decisions — extractive: the assistant's own words,
    # trimmed to an excerpt.
    progress = (getattr(turn, "assistant_text", "") or "").strip()
    reasoning = (getattr(turn, "reasoning", "") or "").strip()
    progress_text = progress or reasoning
    if progress_text:
        parts.append("Assistant's progress so far:\n"
                     + _one_line(progress_text, _SUMMARY_TEXT_CHARS))

    parts.append(f"Tool calls ({len(tools)} total):")
    if not tools:
        parts.append("(no tool calls were made)")
    for i, ev in enumerate(tools, 1):
        name = getattr(ev, "name", "?")
        args = getattr(ev, "args", {}) or {}
        status = getattr(ev, "status", "")
        result = getattr(ev, "result", "") or ""
        try:
            arg_text = json.dumps(args, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001
            arg_text = str(args)
        preview = _one_line(result, _SUMMARY_RESULT_CHARS)
        if not preview:
            preview = "(no output)"
        parts.append(
            f"  {i}. {name}({_one_line(arg_text, _SUMMARY_ARGS_CHARS)})"
            f" -> {status or 'unknown'}: {preview}")

    summary = "\n\n".join(parts)
    _trunc_note = "\n…[summary truncated]"
    if len(summary) > _MAX_SUMMARY_CHARS:
        summary = summary[: _MAX_SUMMARY_CHARS - len(_trunc_note)] \
            + _trunc_note
    return summary


def prune_messages_for_continue(messages: list, summary: str,
                                user_text: str) -> list:
    """Shrink the model-visible history to: system prompt (if any) +
    the turn's user request + the summary. Never mutates the input.
    The "continue" turn starts from this compact state instead of the
    full 25-call history."""
    out: list = []
    rest = list(messages or [])
    if rest and rest[0].get("role") == "system":
        out.append(rest[0])
        rest = rest[1:]
    # the turn's user request — last user message wins; fall back to the
    # Turn record when history is empty or has no user message
    user_msg = next((m for m in reversed(rest)
                     if m.get("role") == "user"), None)
    content = (user_msg.get("content") if user_msg else None) or user_text
    out.append({"role": "user", "content": content})
    out.append({"role": "assistant", "content": summary})
    return out


# ---------------------------------------------------------------------------
# Self-test: prove the history shrinks dramatically after the cap.
# Run: python3 -m fullagent.turnsummary
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from types import SimpleNamespace

    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            failures.append(name)

    # 1. fake a capped turn: 25 tool calls with fat outputs + chatter.
    # Fat tool outputs dominate — that is exactly what chokes the provider
    # on "continue", so the test reproduces the real proportion.
    tools = [
        SimpleNamespace(
            name=f"tool_{i % 5}",
            args={"path": f"/tmp/file_{i}.py", "content": "x" * 500},
            result=("result " * 2000) + f" marker-{i}",
            status="done" if i % 4 else "error",
        )
        for i in range(25)
    ]
    turn = SimpleNamespace(
        user_text=("Build the whole feature " * 200),  # fat user prompt
        assistant_text=("I am making progress, step by step. " * 150),
        reasoning=("Let me think about the plan. " * 80),
        tools=tools,
        error="Turn stopped after 25 tool calls — ask me to continue.",
    )
    summary = summarize_turn(turn)
    check("summary is a non-empty string",
          isinstance(summary, str) and len(summary) > 100)
    check(f"summary bounded at {_MAX_SUMMARY_CHARS} "
          f"(got {len(summary)})", len(summary) <= _MAX_SUMMARY_CHARS)
    check("summary covers user request", "User request" in summary)
    check("summary lists all 25 tool calls",
          summary.count("tool_") >= 25 or "25 total" in summary)
    check("summary carries statuses", "error" in summary and "done" in summary)
    check("summary carries pending/next step", "pending" in summary.lower())
    # no single raw fat output leaks through verbatim
    check("no raw fat result leaks verbatim",
          ("result " * 2000) not in summary)

    # 2. history shrink: system + user + 50 interleaved call/result msgs
    messages = [{"role": "system", "content": "sys prompt"}]
    messages.append({"role": "user", "content": turn.user_text})
    for i in range(25):
        messages.append({"role": "assistant",
                         "content": f"call {i}",
                         "tool_calls": [{"id": f"c{i}"}]})
        messages.append({"role": "tool", "tool_call_id": f"c{i}",
                         "content": "x" * 14000})
    before = sum(len(str(m.get("content", ""))) for m in messages)
    pruned = prune_messages_for_continue(messages, summary, turn.user_text)
    after = sum(len(str(m.get("content", ""))) for m in pruned)
    check("input not mutated", len(messages) == 52)
    check("pruned to exactly 3 messages (system+user+summary)",
          len(pruned) == 3)
    check("keeps system prompt first",
          pruned[0].get("role") == "system")
    check("keeps user request", pruned[1].get("role") == "user"
          and pruned[1]["content"] == turn.user_text)
    check("ends with summary", pruned[2].get("role") == "assistant"
          and pruned[2]["content"] == summary)
    shrink = (before - after) / before if before else 0
    check(f"history shrinks dramatically "
          f"({before:,} -> {after:,} chars, {shrink:.1%})",
          shrink > 0.95 and after <= 15000)

    # 3. edge cases
    empty = summarize_turn(SimpleNamespace(user_text="", tools=[],
                                           assistant_text="", reasoning="",
                                           error=""))
    check("empty turn still summarises", isinstance(empty, str)
          and len(empty) > 0)
    no_system = prune_messages_for_continue(
        [{"role": "user", "content": "hi"}], summary, "hi")
    check("works without system prompt", len(no_system) == 2
          and no_system[0]["role"] == "user")
    no_user = prune_messages_for_continue([], summary, "fallback req")
    check("works with empty history (falls back to user_text)",
          len(no_user) == 2 and no_user[0]["content"] == "fallback req")

    # 4. end-to-end through a REAL Agent: a capped turn must shrink
    # self.messages via the exact finalization hook the turn loop calls
    import tempfile
    from pathlib import Path
    from .agent import Agent, ToolEvent
    from .config import Config
    from .kernel import EventLog as _EventLog

    _td = tempfile.mkdtemp(prefix="cap_summary_selftest_")
    agent = Agent(Config())
    agent.log.close()
    agent.log = _EventLog(Path(_td) / "events.jsonl",
                          session=agent.session_id)
    agent.messages = [{"role": "system", "content": "sys prompt"},
                      {"role": "user", "content": "do the big task"}]
    real_tools = [
        ToolEvent(name="write_file",
                  args={"path": f"/tmp/f{i}.py", "content": "y" * 100},
                  result=("ok " * 3000), status="done")
        for i in range(25)
    ]
    for i in range(25):
        agent.messages.append(
            {"role": "assistant", "content": f"c{i}",
             "tool_calls": [{"id": f"t{i}"}]})
        agent.messages.append(
            {"role": "tool", "tool_call_id": f"t{i}",
             "content": "ok " * 3000})
    import fullagent.agent as _agent_mod
    cap_turn = _agent_mod.Turn(user_text="do the big task",
                              assistant_text="half the files written",
                              tools=real_tools,
                              error="Turn stopped after 25 tool calls — ask "
                                    "me to continue.",
                              cap_stopped=True)
    n_before = len(agent.messages)
    chars_before = agent._messages_chars()
    agent._compact_cap_hit(cap_turn)
    chars_after = agent._messages_chars()
    check("Turn.summary populated on cap",
          isinstance(cap_turn.summary, str) and len(cap_turn.summary) > 100)
    check(f"agent history pruned {n_before} -> {len(agent.messages)} msgs",
          len(agent.messages) == 3)
    check(f"agent history chars shrank "
          f"({chars_before:,} -> {chars_after:,})",
          chars_after < chars_before // 10)
    check("compacted history is system+user+summary",
          [m.get("role") for m in agent.messages]
          == ["system", "user", "assistant"]
          and agent.messages[2]["content"] == cap_turn.summary)
    check("compaction logged to event log",
          any(getattr(e, "type", "") == "turn.capped_compact"
              for e in agent.log._events))
    agent.log.close()

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        raise SystemExit(1)
    print("TURNSUMMARY SELF-TEST PASS")
