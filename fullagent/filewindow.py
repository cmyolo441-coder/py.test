"""File-content sliding window for the model-visible history.

PERF: a read_file result parks the whole file window in the message
history FOREVER, so every subsequent API call of the turn re-sends it.
With N file reads per turn this is quadratic bloat — a 25-call turn
spent ~460s partly on exactly this.

This module implements a sliding window over file-read tool results:
only the most recent `keep` file reads stay in full; older ones are
replaced by a compact digest (path + total line count + window range +
first 5 content lines + sha256 of the full text). The model can still
re-read from disk, and the digest proves what was there.

Rules:
  * Operates on the MODEL-VISIBLE view only (what prune_messages
    produces). Canonical history, event log and checkpoints keep the
    full text — the record is never rewritten.
  * NEVER mutates the input list or its messages: compacted messages
    are shallow-copied before their content is replaced.
  * NEVER touches role/tool_call_id/message order, so tool_call <->
    tool-response pairing stays valid and providers never see a
    pairing error. Only the `content` string of stale file-read
    responses is swapped.
  * A file-read response is identified by pairing each tool message
    with its assistant tool_call (via tool_call_id) and checking the
    tool NAME — never by sniffing content — so an error string or a
    non-read tool that happens to look like a file never gets a
    wrong digest.
  * keep <= 0 compacts everything; keep larger than the number of
    file reads compacts nothing (transparent recent reads).
"""

import hashlib
import re

# Tool names whose results are file contents and therefore get the
# sliding window. read_region delegates to read_file internally but is
# registered under its own name — cover both.
_READ_TOOLS = frozenset({"read_file", "read_region", "read"})

# read_file result header, e.g.:
#   [/path/to/f.py — 123 lines total, showing 1..200]
_HEADER_RE = re.compile(
    r"^\[(?P<path>.+?) \u2014 (?P<total>\d+) lines total, "
    r"showing (?P<start>\d+)\.\.(?P<end>\d+)\]"
)

_DEFAULT_KEEP = 3
_PREVIEW_LINES = 5


def _call_names(messages: list) -> dict:
    """Map tool_call_id -> tool name from assistant messages."""
    names: dict = {}
    for m in messages:
        calls = m.get("tool_calls")
        if not calls:
            continue
        for tc in calls:
            cid = tc.get("id")
            fn = tc.get("function") or {}
            if cid:
                names[cid] = fn.get("name", "")
    return names


def digest_summary(content: str, tool_name: str = "read_file") -> str:
    """Compact digest for a stale file-read result.

    path + line count + window range + first 5 content lines + sha256
    of the full text, plus an explicit re-read hint for the model.
    """
    digest = hashlib.sha256(
        content.encode("utf-8", "replace")).hexdigest()[:12]
    m = _HEADER_RE.match(content)
    if m:
        path = m.group("path")
        total = m.group("total")
        start = m.group("start")
        end = m.group("end")
        # content lines follow the header line; keep line numbers intact
        preview = "\n".join(content.splitlines()[1:1 + _PREVIEW_LINES])
        return (
            f"[{tool_name} {path} \u2014 {total} lines total (window "
            f"{start}..{end} compacted by file sliding window; "
            f"sha256 {digest}). First {_PREVIEW_LINES} lines:\n{preview}\n"
            f"\u2026 full content re-readable via "
            f"{tool_name}({path!r}, offset, limit) if needed]")
    preview = "\n".join(content.splitlines()[:_PREVIEW_LINES])
    return (
        f"[{tool_name} file-read compacted by sliding window; "
        f"sha256 {digest}. First {_PREVIEW_LINES} lines:\n{preview}\n"
        f"\u2026 re-read from disk if needed]")


def compact_file_reads(messages: list, keep: int = _DEFAULT_KEEP) -> list:
    """Sliding window over file-read tool results.

    Returns a NEW list: the most recent `keep` file-read tool responses
    stay verbatim, older ones are replaced by digests. Pairing-critical
    fields (role, tool_call_id, order) are never altered; compacted
    messages are shallow copies, input is never mutated.
    """
    if not messages or keep < 0:
        return list(messages)
    names = _call_names(messages)
    file_idx = [i for i, m in enumerate(messages)
                if m.get("role") == "tool"
                and names.get(m.get("tool_call_id")) in _READ_TOOLS]
    if keep <= 0:
        stale = file_idx
    elif len(file_idx) > keep:
        stale = file_idx[:-keep]
    else:
        stale = []
    if not stale:
        return list(messages)
    out = list(messages)
    for i in stale:
        m = out[i]
        if m.get("role") != "tool":
            continue  # paranoia: never touch a non-tool message
        new = dict(m)  # shallow copy — canonical history untouched
        new["content"] = digest_summary(
            str(new.get("content") or ""),
            tool_name=names.get(new.get("tool_call_id"), "read_file"))
        out[i] = new
    return out


if __name__ == "__main__":
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            failures.append(name)

    def file_content(path: str, n: int) -> str:
        lines = [f"{i+1:>4} line {i+1} of {path}" for i in range(n)]
        header = f"[{path} \u2014 {n} lines total, showing 1..{n}]"
        return header + "\n" + "\n".join(lines)

    # --- scenario: 5 file reads interleaved with other tool calls ---
    msgs: list = [{"role": "system", "content": "sys"}]
    n_files = 5
    for i in range(n_files):
        cid = f"call_{i}"
        msgs.append({
            "role": "assistant", "content": "",
            "tool_calls": [{"id": cid,
                            "function": {"name": "read_file",
                                         "arguments": "{}"}}],
        })
        msgs.append({"role": "tool", "tool_call_id": cid,
                     "content": file_content(f"/src/f{i}.py", 200)})
    # one non-read tool call in the middle of the window
    msgs.append({
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "call_bash",
                        "function": {"name": "bash", "arguments": "{}"}}],
    })
    bash_out = "x" * 3000
    msgs.append({"role": "tool", "tool_call_id": "call_bash",
                 "content": bash_out})

    before_chars = sum(len(str(m.get("content") or "")) for m in msgs)

    out = compact_file_reads(msgs, keep=3)
    after_chars = sum(len(str(m.get("content") or "")) for m in out)

    # layout: msgs[0]=system, then per file i: assistant at 1+2i,
    # tool at 2+2i → file tool msgs at 2,4,6,8,10; stale = 2,4
    _compacted_idx = (2, 4)
    check("returns a new list, input untouched",
          out is not msgs and all(
              msgs[i].get("content") == out[i].get("content")
              or i in _compacted_idx
              for i in range(len(msgs))))
    # canonical messages must be byte-identical objects for untouched ones
    untouched = [i for i in range(len(msgs)) if i not in _compacted_idx]
    check("untouched messages are the same objects",
          all(out[i] is msgs[i] for i in untouched))

    # recent 3 file reads stay full
    check("most recent 3 file reads verbatim",
          all(out[i]["content"] == msgs[i]["content"]
              for i in (6, 8, 10)))
    # older 2 file reads compacted
    old = [out[2]["content"], out[4]["content"]]
    check("older 2 file reads compacted (digest marker)",
          all("compacted by file sliding window" in str(c) for c in old))
    check("digest keeps path, line count and sha256",
          all("/src/f" in str(c) and "200 lines total" in str(c)
              and "sha256" in str(c) for c in old))
    check("digest keeps first 5 lines",
          all("line 5 of" in str(c) and "line 6 of" not in str(c)
              for c in old))
    check("non-read tool output untouched (bash result kept)",
          out[-1]["content"] == bash_out)

    # pairing validity: every assistant tool_call id still answered,
    # role/order/tool_call_id preserved
    cids = {tc["id"] for m in out if m.get("role") == "assistant"
            for tc in (m.get("tool_calls") or [])}
    answered = {m.get("tool_call_id") for m in out
                if m.get("role") == "tool"}
    check("tool_call/tool pairing intact", cids <= answered)
    check("message order and roles unchanged",
          [m.get("role") for m in out]
          == [m.get("role") for m in msgs])
    check("tool_call_ids unchanged",
          [m.get("tool_call_id") for m in out
           if m.get("role") == "tool"]
          == [m.get("tool_call_id") for m in msgs
              if m.get("role") == "tool"])

    _before_stale = sum(len(str(msgs[i]["content"])) for i in (2, 4))
    _after_stale = sum(len(str(out[i]["content"])) for i in (2, 4))
    check("stale file reads shrink to digests (>80% cut on stale portion)",
          _after_stale < _before_stale * 0.2)
    print(f"      stale chars {_before_stale} -> {_after_stale}; "
          f"total {before_chars} -> {after_chars}")

    # --- edge cases ---
    check("keep larger than reads: nothing compacted",
          all(compact_file_reads(msgs, keep=99)[i].get("content")
              == msgs[i].get("content") for i in range(len(msgs))))
    check("keep=0 compacts all file reads",
          all("compacted by file sliding window" in
              str(compact_file_reads(msgs, keep=0)[i]["content"])
              for i in (2, 4, 6, 8, 10))
          and "compacted by file sliding window" not in
              str(compact_file_reads(msgs, keep=0)[-1]["content"]))
    check("empty input returns empty", compact_file_reads([]) == [])
    # error-string read result (no header): still compacted, never crashes
    err_msgs = [
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "c1",
                         "function": {"name": "read_file",
                                      "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1",
         "content": "ERROR: file not found: /nope.py"},
    ]
    r = compact_file_reads(err_msgs, keep=0)
    check("error result compacts without crash",
          "sha256" in str(r[1]["content"]) and r[1]["tool_call_id"] == "c1")
    # orphan tool message (no matching assistant call): left alone
    orphan = [{"role": "tool", "tool_call_id": "zzz",
               "content": "some content"}]
    check("orphan tool message never compacted",
          compact_file_reads(orphan, keep=0)[0]["content"] == "some content")
    # non-string content: coerced, no crash
    ns = [{"role": "assistant", "content": "",
           "tool_calls": [{"id": "c2",
                           "function": {"name": "read_file",
                                        "arguments": "{}"}}]},
          {"role": "tool", "tool_call_id": "c2", "content": None}]
    r2 = compact_file_reads(ns, keep=0)
    check("None content coerced safely",
          isinstance(r2[1]["content"], str)
          and r2[1]["tool_call_id"] == "c2")

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        raise SystemExit(1)
    print("FILEWINDOW SELF-TEST PASS")
