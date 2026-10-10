"""Tool-result truncation before message history (worker 4/20, speed sprint).

Problem: a 25-call turn keeps every tool result VERBATIM in
``self.messages`` (read_file of a huge file, run_command with pages of
output) and that full history is re-sent to the provider on every model
call — 25 calls x megabytes of output = 460s turns.

Fix: truncate each tool result AT INSERTION TIME (agent.py `_finish_one`),
before it enters history:

* cap at ``MAX_RESULT_CHARS`` (4000), keeping ``HEAD_CHARS`` (2000) +
  ``TAIL_CHARS`` (2000) with a ``…[truncated N chars]`` marker so the
  model still sees the start (context) and the end (usually the summary /
  exit status of a command run).
* results with status error/blocked/denied are kept VERBATIM — they are
  short and critical (stack traces, permission blocks).
* results a tool explicitly needs in full can opt out via
  ``keep_full=True`` (per-event) or the ``ALWAYS_FULL_TOOLS`` registry.

Only the history copy is truncated — ``ev.result`` itself stays full so
the TUI, event log, loop detection and retry hints all see the complete
output. Pairing is untouched: the message dict keeps ``role``/``tool_call_id``
exactly as before, only ``content`` is shortened.
"""

MAX_RESULT_CHARS = 4000   # cap per tool result in history
HEAD_CHARS = 2000         # chars kept from the start
TAIL_CHARS = 2000         # chars kept from the end

# statuses whose results are always kept verbatim — short and critical
FULL_RESULT_STATUSES = frozenset({"error", "blocked", "denied"})

# tool names whose results are never truncated (model needs them whole);
# populated by tools at import time via register_full_tool()
ALWAYS_FULL_TOOLS: set[str] = set()

_TRUNC_TAG = "[truncated "


def register_full_tool(name: str) -> None:
    """Opt a tool out of history truncation (it needs full results)."""
    if name:
        ALWAYS_FULL_TOOLS.add(name)


def unregister_full_tool(name: str) -> None:
    ALWAYS_FULL_TOOLS.discard(name)


def is_truncated(content: str) -> bool:
    """True when content carries a truncation marker from this module."""
    return isinstance(content, str) and _TRUNC_TAG in content


def _marker(dropped: int) -> str:
    return (f"\n…[truncated {dropped:,} chars — the middle was removed; "
            f"re-run the tool with narrower arguments or read the file "
            f"again if you need it]\n")


def truncate_tool_result(content, status: str = "done",
                         tool_name: str = "",
                         keep_full: bool = False) -> str:
    """Return the history-safe copy of a tool result.

    Verbatim when: the result is short, the status is in
    FULL_RESULT_STATUSES (error/blocked/denied), keep_full is set, or
    the tool is registered in ALWAYS_FULL_TOOLS. Otherwise head+tail
    truncated with a marker carrying the dropped char count.
    """
    if content is None:
        return ""
    if not isinstance(content, str):
        content = str(content)
    if (len(content) <= MAX_RESULT_CHARS
            or (status or "") in FULL_RESULT_STATUSES
            or keep_full
            or (tool_name or "") in ALWAYS_FULL_TOOLS):
        return content
    head = content[:HEAD_CHARS]
    tail = content[-TAIL_CHARS:]
    dropped = len(content) - HEAD_CHARS - TAIL_CHARS
    return head + _marker(dropped) + tail


if __name__ == "__main__":
    n = 0

    def check(cond, label):
        global n
        assert cond, f"FAILED: {label}"
        n += 1

    # 1. Huge 'done' output gets head+tail truncated with an exact marker.
    big = "".join(f"line {i:06d}: some output text here\n" for i in range(3000))
    assert len(big) > 100_000
    t = truncate_tool_result(big, status="done", tool_name="run_command")
    check(len(t) <= MAX_RESULT_CHARS + len(_marker(0)) + 40, "capped size")
    check(t.startswith(big[:HEAD_CHARS]), "head preserved")
    check(t.endswith(big[-TAIL_CHARS:]), "tail preserved")
    dropped = len(big) - HEAD_CHARS - TAIL_CHARS
    check(f"[truncated {dropped:,} chars" in t, "marker has dropped count")
    check(is_truncated(t), "is_truncated detects marker")

    # 2. Boundary: exactly at cap passes through unchanged.
    exact = "x" * MAX_RESULT_CHARS
    check(truncate_tool_result(exact) == exact, "exact-cap identity")
    check(truncate_tool_result(exact + "y") != exact + "y", "cap+1 truncates")

    # 3. Short output passes through identical (same object, no copy).
    short = "ok: 42\n"
    check(truncate_tool_result(short, status="done") is short,
          "short output identity")

    # 4. Error/blocked/denied results stay VERBATIM even when huge.
    huge_err = "ERROR: " + "boom " * 50_000
    for st in ("error", "blocked", "denied"):
        check(truncate_tool_result(huge_err, status=st) == huge_err,
              f"{st} kept verbatim")
        check(not is_truncated(truncate_tool_result(huge_err, status=st)),
              f"{st} has no marker")

    # 5. Per-event opt-out via keep_full.
    check(truncate_tool_result(big, status="done", keep_full=True) == big,
          "keep_full opt-out")

    # 6. Registry opt-out via register_full_tool / unregister_full_tool.
    register_full_tool("read_file")
    try:
        check(truncate_tool_result(big, status="done",
                                   tool_name="read_file") == big,
              "registered tool kept full")
        check(truncate_tool_result(big, status="done",
                                   tool_name="run_command") != big,
              "unregistered tool still truncated")
    finally:
        unregister_full_tool("read_file")
    check(truncate_tool_result(big, status="done",
                               tool_name="read_file") != big,
          "unregistered tool truncates again")

    # 7. Pairing: mirror of _finish_one's append — tool_call_id pairing
    #    must survive truncation (only content changes, never the id).
    def _finish_one_append(messages, call_id, result, status):
        # mirrors agent.py _finish_one's history insert exactly
        content = truncate_tool_result(result, status=status,
                                       tool_name="run_command")
        messages.append({"role": "tool", "tool_call_id": call_id,
                         "content": content})

    messages = []
    call_ids = [f"call_{i}" for i in range(5)]
    for i, cid in enumerate(call_ids):
        st = "error" if i == 2 else "done"
        _finish_one_append(messages, cid, big if i != 2 else huge_err, st)
    check(len(messages) == 5, "one message per call")
    for m, cid in zip(messages, call_ids):
        check(m["role"] == "tool" and m["tool_call_id"] == cid,
              f"pairing intact for {cid}")
    check(all(m["tool_call_id"] for m in messages), "no empty ids")
    answered = {m["tool_call_id"] for m in messages}
    check(answered == set(call_ids), "1:1 id mapping")

    # 8. Non-str / None inputs are defensive, never crash the turn.
    check(truncate_tool_result(None) == "", "None -> empty")
    check(truncate_tool_result(12345) == "12345", "int coerced")

    # 9. Before/after: a 25-call turn of 24KB outputs (MAX_TOOL_OUTPUT_CHARS).
    per_call = "o" * 24_000
    before = [("call_%d" % i, per_call) for i in range(25)]
    hist_before = [{"role": "tool", "tool_call_id": cid, "content": res}
                   for cid, res in before]
    hist_after = [{"role": "tool", "tool_call_id": cid,
                   "content": truncate_tool_result(res, status="done")}
                  for cid, res in before]
    size_before = sum(len(m["content"]) for m in hist_before)
    size_after = sum(len(m["content"]) for m in hist_after)
    check(size_before == 600_000, "before size 600k")
    check(size_after < size_before * 0.25, "after < 25% of before")
    check(all(m["tool_call_id"] == f"call_{i}"
              for i, m in enumerate(hist_after)), "after pairing intact")
    print(f"resulttrunc before/after: {size_before:,} -> {size_after:,} "
          f"chars ({size_after / size_before:.1%} of original)")
    print(f"resulttrunc self-tests: all {n} assertions passed — PASS")
