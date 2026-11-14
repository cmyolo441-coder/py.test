"""Devin CLI backend: drive `devin acp` (Agent Client Protocol over stdio)
as an OpenAI-shaped model for the swe-2-flash family.

Devin has no public chat-completions endpoint, so this module spawns one
`devin acp --agent-type summarizer --model <id>` process per call, speaks
newline-delimited JSON-RPC (initialize -> session/new -> session/prompt),
flattens the OpenAI-style message history into a single prompt, and parses
the model's <tool_call> markup back into OpenAI-style tool_calls. Auth is
whatever `devin auth login` stored — no API key is read here.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Callable

from . import config
from .client import APIError, StreamResult

_DEVIN_PATH = shutil.which("devin") or os.environ.get("DEVIN_CLI_PATH", "devin")
_AGENT_TYPE = "summarizer"  # tool-less agent: raw model I/O, we own tools

_TOOL_CALL_OPEN = re.compile(r"<tool_call>")
_TOOL_CALL_CLOSE = re.compile(r"</tool_call>")
# The model sometimes closes a <tool_call> block with a stray tag from its
# own markup vocabulary instead of </tool_call> — or omits the closer
# entirely before the next <tool_call>. Any of these ends the block.
_STRAY_CLOSE = re.compile(
    r"</(?:think|arg_value|arg|parameters?|invoke|function|response|json)>\s*")
# Orphan closing tags left in plain text after block extraction.
_ORPHAN_CLOSE = re.compile(
    r"</(?:tool_call|think|arg_value|arg|parameters?|invoke|function|"
    r"response|json)>\s*")
# Inside a block the model writes the call in several shapes:
#   {"name": "x", "arguments": {...}}   read_file{"path": "a"}
#   read_file {"path": "a"}             read_file with {"path": "a"}
#   read_file path: "a" count: 2        (key: value pairs, no JSON at all)
_LOOSE_NAME = re.compile(r"^([A-Za-z_][\w.\-]*)")
_FILLER = re.compile(
    r"^(?:with|using|via|call|invoke|arguments?|args|parameters?|input|to)\b[\s:=]*",
    re.IGNORECASE)
_ARG_PAIR = re.compile(
    r"([A-Za-z_]\w*)\s*[:=]\s*"
    r"(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|\{[^{}]*\}|\[[^\]]*\]|"
    r"true\b|false\b|null\b|-?\d+(?:\.\d+)?|[^\s,]+)")
# A `key:`/`key=` token. The lookbehind keeps `self.path = ...` inside file
# contents from looking like an argument boundary.
_KEY_TOKEN = re.compile(r"(?<![\w.])([A-Za-z_]\w*)\s*[:=]")
_JSON = json.JSONDecoder()


# ---------------------------------------------------------------------------
# ACP client
# ---------------------------------------------------------------------------

class _Acp:
    """One `devin acp` process speaking JSON-RPC over stdio."""

    def __init__(self, model_id: str, cwd: str, timeout: float):
        self._next_id = 0
        self._lines: "queue.Queue[str | None]" = queue.Queue()
        self._stderr: list[str] = []
        self._deadline = time.monotonic() + timeout
        args = ["acp", "--agent-type", _AGENT_TYPE]
        if model_id:
            args += ["--model", model_id]
        try:
            self._proc = subprocess.Popen(
                [_DEVIN_PATH] + args, cwd=cwd or None,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1,
                errors="replace")
        except FileNotFoundError:
            raise APIError(
                f"devin CLI not found ({_DEVIN_PATH}). Install it and run "
                f"`devin auth login`, or pick another provider with /model.",
                status=0) from None
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    # -- io -----------------------------------------------------------------

    def _pump_stdout(self) -> None:
        try:
            for line in self._proc.stdout:  # type: ignore[union-attr]
                self._lines.put(line)
        except Exception:
            pass
        self._lines.put(None)

    def _pump_stderr(self) -> None:
        try:
            for line in self._proc.stderr:  # type: ignore[union-attr]
                self._stderr.append(line)
                if len(self._stderr) > 50:
                    del self._stderr[:25]
        except Exception:
            pass

    def _stderr_tail(self) -> str:
        return "".join(self._stderr[-8:]).strip()[-400:]

    def _remaining(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    def _send(self, obj: dict) -> None:
        try:
            assert self._proc.stdin is not None
            self._proc.stdin.write(json.dumps(obj) + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError, AssertionError) as e:
            # a dead devin process must surface as APIError so the agent's
            # error handling (and its retry layer) sees it — a raw
            # BrokenPipeError escapes every except APIError upstream
            raise APIError(f"devin acp pipe closed: {e}", status=0) from e

    def call(self, method: str, params: dict,
             on_update: Callable[[dict], None] | None = None,
             timeout: float | None = None) -> dict:
        """Send a request; dispatch notifications; return the result.

        ``timeout`` extends the deadline for THIS call — the
        session/prompt generation phase gets a fresh budget instead of
        whatever the initialize/session/new handshake left over."""
        if timeout is not None:
            self._deadline = max(self._deadline,
                                 time.monotonic() + timeout)
        self._next_id += 1
        rid = self._next_id
        self._send({"jsonrpc": "2.0", "id": rid,
                    "method": method, "params": params})
        while True:
            if self._remaining() <= 0:
                self.close()
                raise APIError(f"devin acp: timed out during {method}",
                               status=0)
            try:
                line = self._lines.get(timeout=min(0.5, self._remaining()))
            except queue.Empty:
                continue
            if line is None:
                tail = self._stderr_tail()
                self.close()
                raise APIError(
                    f"devin acp exited during {method}"
                    + (f" (stderr: {tail})" if tail else ""), status=0)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("method"):
                # agent->client request or notification
                if on_update is not None:
                    on_update(msg)
                if "id" in msg:
                    self._send({"jsonrpc": "2.0", "id": msg["id"],
                                "error": {"code": -32601,
                                          "message": "not supported"}})
                continue
            if msg.get("id") != rid:
                continue
            if "error" in msg:
                err = msg["error"] or {}
                detail = err.get("message") or json.dumps(err)
                data = err.get("data") or {}
                if isinstance(data, dict) and data.get("cognition.ai/errorKind"):
                    detail += f" ({data['cognition.ai/errorKind']})"
                self.close()
                raise APIError(f"devin acp {method}: {detail}", status=0)
            return msg.get("result") or {}

    def close(self) -> None:
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.kill()
        except Exception:
            pass
        try:
            self._proc.wait(timeout=5)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Prompt building / response parsing
# ---------------------------------------------------------------------------

def _content_text(content: Any) -> str:
    """OpenAI content is a string or a list of typed parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict):
                if p.get("type") in ("text", "input_text"):
                    parts.append(str(p.get("text", "")))
                elif p.get("type") in ("image_url", "image"):
                    parts.append("[image omitted]")
        return "".join(parts)
    return "" if content is None else str(content)


def _flatten_messages(messages: list[dict]) -> str:
    out: list[str] = []
    for m in messages:
        role = str(m.get("role") or "user")
        if role == "system":
            out.append("# System\n\n" + _content_text(m.get("content")))
            continue
        lines = [f"## {role}"]
        content = _content_text(m.get("content"))
        if content.strip():
            lines.append(content)
        for tc in m.get("tool_calls") or []:
            fn = (tc or {}).get("function") or {}
            if isinstance(fn, str):
                fn = {"name": fn, "arguments": "{}"}
            lines.append(f"[called tool {fn.get('name', '?')} "
                         f"with {fn.get('arguments', '{}')}]")
        out.append("\n".join(lines))
        # OpenAI tool results are separate role="tool" messages.
    # tool results carry tool_call_id; render them inline after nothing —
    # they already appear as their own ## tool sections below.
    return "\n\n".join(out) + "\n"


def build_prompt(model_id: str, messages: list[dict],
                 tools: list[dict] | None) -> str:
    b: list[str] = []
    sys_msgs = [m for m in messages if m.get("role") == "system"]
    if sys_msgs:
        b.append("# System\n\n" + "\n\n".join(
            _content_text(m.get("content")) for m in sys_msgs))
    convo = [m for m in messages if m.get("role") != "system"]
    if tools:
        b.append(
            "# Tools\n\n"
            "You may call tools. To call one or more tools, output blocks "
            "exactly like this and nothing else inside them:\n"
            '<tool_call>{"name": "read_file", "arguments": {"path": '
            '"testfile.txt"}}</tool_call>\n'
            'The JSON object MUST have a "name" string field and an '
            '"arguments" object field. Do not wrap the block in markdown '
            "fences. Emit the block as plain text only — do NOT actually "
            "perform the action or use any built-in capability yourself. "
            "If no tool is needed, answer normally.\n\n"
            "Available tools (JSON Schema):")
        for t in tools:
            fn = t.get("function") or t
            entry = {"name": fn.get("name"),
                     "description": fn.get("description", ""),
                     "parameters": fn.get("parameters") or {}}
            b.append(json.dumps(entry, ensure_ascii=False))
        b.append("")
    b.append("# Conversation\n")
    b.append(_flatten_messages(convo))
    return "\n".join(b)


def _json_span(text: str, start: int):
    """Locate a JSON payload inside a <tool_call> body.

    Returns (json_start, json_end) when a JSON object/array can be decoded
    exactly — either the body itself starts with ``{``/``[`` or a loose
    ``name {json}`` / ``name with {json}`` form follows. Returns None for
    ``key: value`` bodies, which have no JSON to anchor on. Decoding with
    raw_decode means markup literals (``</tool_call>``, ``</function>``)
    inside string arguments can NEVER truncate the block — the #1 cause
    of corrupted writes and stranded turns.
    """
    j = start
    while j < len(text) and text[j] in " \t\r\n":
        j += 1
    if j >= len(text):
        return None
    candidates = [j] if text[j] in "{[" else []
    # ^-anchored patterns can't use pos= — slice instead
    m = _LOOSE_NAME.match(text[j:])
    if m:
        k = j + m.end()
        # peel filler words between the tool name and its JSON args
        while True:
            while k < len(text) and text[k] in " \t\r\n":
                k += 1
            fm = _FILLER.match(text[k:])
            if not fm:
                break
            k += fm.end()
        if k < len(text) and text[k] in "{[":
            candidates.append(k)
    for c in candidates:
        try:
            _obj, end = _JSON.raw_decode(text, c)
            return c, end
        except ValueError:
            continue
    return None


def _parse_tool_calls(text: str, schemas: dict | None = None):
    """Split streamed text into (plain_text, [tool_call dicts]).

    A block ends at the FIRST of: </tool_call>, a stray closing tag the
    model invented (</think>, </arg_value>, ...), the next <tool_call>, or
    end of text. Without the stray/next-open terminators an unclosed block
    swallows every following call and the args JSON corrupts.

    When the body contains a decodable JSON payload the block boundary is
    computed JSON-aware (see _json_span) so markup inside string arguments
    is harmless; the legacy terminator scan is only a fallback for
    brace-free ``key: value`` bodies.

    schemas maps tool name -> set of declared parameter names; when the
    model writes loose `key: value` arguments, only declared parameters
    are accepted so file contents can't be misparsed as arguments.
    """
    blocks: list[str] = []
    calls: list[dict] = []
    opens = list(_TOOL_CALL_OPEN.finditer(text))
    pos = 0
    for i, m in enumerate(opens):
        if m.start() < pos:
            continue
        seg = text[pos:m.start()].strip()
        if seg:
            blocks.append(seg)
        start = m.end()
        span = _json_span(text, start)
        if span is not None:
            _js, jend = span
            raw = text[start:jend].strip()
            # consume optional whitespace + one closing tag
            k = jend
            while k < len(text) and text[k] in " \t\r\n":
                k += 1
            cm = _TOOL_CALL_CLOSE.match(text, k)
            if cm is None:
                cm = _STRAY_CLOSE.match(text, k)
            block_end = cm.end() if cm else jend
        else:
            ends = []
            c = _TOOL_CALL_CLOSE.search(text, start)
            if c:
                ends.append((c.start(), c.end()))
            s = _STRAY_CLOSE.search(text, start)
            if s:
                ends.append((s.start(), s.end()))
            if i + 1 < len(opens):
                ends.append((opens[i + 1].start(), opens[i + 1].start()))
            if ends:
                content_end, block_end = min(ends)
            else:
                content_end = block_end = len(text)
            raw = text[start:content_end].strip()
        valid = (schemas or {}).get(_guess_tool_name(raw))
        tc = _parse_one_call(raw, valid)
        if tc is not None:
            calls.append(tc)
        elif raw:
            blocks.append(text[m.start():block_end].strip())
        pos = block_end
    if pos < len(text):
        seg = text[pos:].strip()
        if seg:
            blocks.append(seg)
    return _ORPHAN_CLOSE.sub("", "\n\n".join(blocks)).strip(), calls


# The flattened history renders past assistant tool calls as
# "[called tool NAME with {args}]" (see _flatten_messages). The model
# sometimes imitates THAT notation instead of emitting <tool_call>
# markup, so the "call" arrives as plain text, no tool_calls come back,
# and the turn ends after one tool — the recurring "agent stops" bug.
# _extract_inline_calls recovers those into real tool calls.
_INLINE_CALL = re.compile(
    r"\[\s*called\s+tool\s+([A-Za-z_][\w.\-]*)\s+with\s+", re.IGNORECASE)


def _mk_call(name: str, args: dict) -> dict:
    _parse_one_call._seq[0] += 1  # type: ignore[attr-defined]
    return {"id": f"call_{_parse_one_call._seq[0]}",  # type: ignore[attr-defined]
            "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(args, ensure_ascii=False)}}


def _extract_inline_calls(text: str, schemas: dict | None):
    """Recover ``[called tool NAME with ARGS]`` spans written as plain text.

    Returns (clean_text, [tool_call dicts]). A span only converts when
    NAME is a declared tool (when schemas are known) and ARGS parses as a
    JSON object or loose ``key: value`` pairs — quoted/log text that merely
    looks similar stays untouched. The matched span is removed from the
    text so it never reaches the user as literal markup.
    """
    if not text or not schemas or "[called tool" not in text.lower():
        return text, []
    # case-insensitive tool lookup — the model may capitalise the name
    lookup = {k.lower(): k for k in schemas}
    out: list[str] = []
    calls: list[dict] = []
    pos = 0
    for m in _INLINE_CALL.finditer(text):
        if m.start() < pos:
            continue
        name = m.group(1)
        real_name = lookup.get(name.lower())
        # Only declared tools convert — anything else is prose quoting
        # the notation, not a real call.
        if real_name is None:
            continue
        valid = schemas[real_name]
        rest = text[m.end():]
        lead = len(rest) - len(rest.lstrip())
        args_obj = None
        end = -1
        try:
            obj, end = _JSON.raw_decode(rest, lead)
            if isinstance(obj, dict):
                args_obj = obj
        except ValueError:
            pass
        if args_obj is None:
            # loose `key: value` form — the span ends at the LAST "]"
            # before the next "[called tool" marker (or end of text), so
            # a "]" inside a quoted arg value can't truncate the args
            nxt = text.lower().find("[called tool", m.end())
            region_end = nxt if nxt > 0 else len(text)
            close = text.rfind("]", m.end(), region_end)
            if close < 0:
                continue
            parsed = _parse_loose_args(text[m.end():close].strip(), valid)
            if not isinstance(parsed, dict):
                continue
            args_obj = parsed
            end = close - m.end() + 1
        else:
            # consume a trailing "]" when the model closed the span
            tail = rest[end:]
            stripped = tail.lstrip()
            if stripped.startswith("]"):
                end += len(tail) - len(stripped) + 1
        calls.append(_mk_call(real_name, args_obj))
        out.append(text[pos:m.start()])
        # `end` is relative to `rest`, which starts at m.end()
        pos = m.end() + end
    if not calls:
        return text, []
    out.append(text[pos:])
    return "".join(out).strip(), calls


def _guess_tool_name(raw: str) -> str:
    """Best-effort tool name from a raw <tool_call> body, for schema lookup."""
    text = raw.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and obj.get("name"):
            return str(obj["name"])
    except (ValueError, TypeError):
        pass
    m = _LOOSE_NAME.match(text)
    return m.group(1) if m else ""


def _parse_one_call(raw: str, valid: set | None = None) -> dict | None:
    name, args = "", "{}"
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            name = str(obj.get("name") or "")
            a = obj.get("arguments", {})
            args = a if isinstance(a, str) else json.dumps(a, ensure_ascii=False)
    except (ValueError, TypeError):
        text = raw.strip()
        m = _LOOSE_NAME.match(text)
        if not m:
            return None
        name = m.group(1)
        rest = text[m.end():].strip()
        parsed = _parse_loose_args(rest, valid)
        if parsed is not None:
            args = json.dumps(parsed, ensure_ascii=False)
    if not name:
        return None
    _parse_one_call._seq[0] += 1
    return {"id": f"call_{_parse_one_call._seq[0]}",
            "type": "function",
            "function": {"name": name, "arguments": args or "{}"}}


_parse_one_call._seq = [0]  # type: ignore[attr-defined]


def _parse_loose_args(rest: str, valid: set | None = None):
    """Parse the text after a loose tool name into an arguments dict.

    Accepts a JSON object (with optional filler like "with" before it) or
    `key: value` / `key=value` pairs. When `valid` (the tool's declared
    parameter names) is given, only those keys are accepted and each
    value runs to the next declared key — so a big `content="..."` blob
    keeps its embedded `x = y` lines instead of exploding into bogus
    arguments. Returns None when nothing parseable is present.
    """
    rest = rest.strip()
    # peel filler words: "with", "arguments", "args", ... (repeatedly)
    while True:
        m = _FILLER.match(rest)
        if not m:
            break
        rest = rest[m.end():].lstrip(" \t:=")
    if not rest:
        return None
    if rest.startswith("{") or rest.startswith("["):
        try:
            obj, _ = _JSON.raw_decode(rest)
            if isinstance(obj, dict):
                return obj
            if isinstance(obj, list) and obj and all(
                    isinstance(p, dict) for p in obj):
                # [ {"key": "value"}, ... ] -> merge single-pair objects
                merged = {}
                for p in obj:
                    merged.update(p)
                return merged
        except ValueError:
            pass
    return _parse_pairs(rest, valid)


def _parse_pairs(rest: str, valid: set | None):
    """Parse `key: value` / `key=value` pairs.

    With `valid` set, only declared parameter names count as keys and a
    value runs until the next declared key — file contents survive
    intact. Without it, every `word:`/`word=` token is a boundary (the
    old, lossier behaviour kept as a fallback).
    """
    matches = None
    if valid:
        matches = [m for m in _KEY_TOKEN.finditer(rest)
                   if m.group(1) in valid]
    if not matches:
        # No declared keys found — either no schema or the model invented
        # its own argument names. Fall back to every token as a boundary.
        matches = list(_KEY_TOKEN.finditer(rest))
    if not matches:
        return None
    out = {}
    for i, m in enumerate(matches):
        k = m.group(1)
        vend = matches[i + 1].start() if i + 1 < len(matches) else len(rest)
        out[k] = _coerce_value(rest[m.end():vend].strip())
    return out or None


def _coerce_value(v: str):
    """Unquote and type a loose `key: value` argument value."""
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        inner = v[1:-1]
        if v[0] == '"':
            try:
                return json.loads(v)
            except ValueError:
                return inner
        return inner
    if v == "true":
        return True
    if v == "false":
        return False
    if v == "null":
        return None
    if v[:1] in "{[":
        try:
            obj, _ = _JSON.raw_decode(v)
            return obj
        except ValueError:
            return v
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


# ---------------------------------------------------------------------------
# Entry points (same signatures agent.py uses from client.py)
# ---------------------------------------------------------------------------

def _run(provider, model: config.Model, messages: list[dict],
         tools: list[dict] | None, timeout: float,
         on_token, on_reasoning, on_tool_start, on_tool_args,
         should_cancel, on_status) -> StreamResult:
    if should_cancel is not None and should_cancel():
        from .cancelguard import TurnCancelled
        raise TurnCancelled("devin call cancelled by user")

    prompt = build_prompt(model.id, messages, tools)
    text_parts: list[str] = []
    think_parts: list[str] = []
    # One retry for transient subprocess failures (spawn race, pipe
    # closed, ACP timeout) — the HTTP path retries via chat_with_retry,
    # this path bypasses it, so a single flaky `devin acp` used to kill
    # the whole turn.
    result = None
    last_err: APIError | None = None
    for attempt in range(2):
        acp = _Acp(model.id, os.getcwd(), timeout)
        # a retry must not duplicate the partial stream from attempt 1
        del text_parts[:]
        del think_parts[:]

        def on_msg(msg: dict) -> None:
            if msg.get("method") != "session/update":
                return
            upd = (msg.get("params") or {}).get("update") or {}
            kind = upd.get("sessionUpdate")
            content = upd.get("content") or {}
            chunk = content.get("text") or ""
            if not chunk:
                return
            if kind == "agent_message_chunk":
                text_parts.append(chunk)
                if on_token:
                    on_token(chunk)
            elif kind == "agent_thought_chunk":
                think_parts.append(chunk)
                if on_reasoning:
                    on_reasoning(chunk)

        try:
            acp.call("initialize", {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": False}}, on_update=on_msg)
            session = acp.call("session/new", {
                "cwd": os.getcwd(), "mcpServers": []}, on_update=on_msg)
            sid = session.get("sessionId")
            if not sid:
                raise APIError(
                    "devin acp: session/new returned no sessionId",
                    status=0)
            # generation gets its OWN full budget — the shared deadline
            # used to let a slow handshake starve session/prompt down to
            # seconds and kill long turns at the 300s mark
            result = acp.call("session/prompt", {
                "sessionId": sid,
                "prompt": [{"type": "text", "text": prompt}]},
                on_update=on_msg, timeout=timeout)
            break
        except APIError as e:
            last_err = e
            transient = ("timed out" in str(e) or "exited" in str(e)
                         or "pipe closed" in str(e))
            if attempt == 0 and transient:
                time.sleep(0.5)
                continue
            raise
        finally:
            acp.close()
    if result is None:
        raise last_err or APIError("devin acp: no result", status=0)

    if should_cancel is not None and should_cancel():
        from .cancelguard import TurnCancelled
        raise TurnCancelled("devin call cancelled by user")

    raw = "".join(text_parts)
    schemas = _tool_schemas(tools)
    content, calls = _parse_tool_calls(raw, schemas)
    # The model sometimes imitates the flattened-history notation
    # "[called tool NAME with {...}]" instead of <tool_call> markup —
    # recover those so the turn keeps going instead of ending early.
    content, inline = _extract_inline_calls(content, schemas)
    calls.extend(inline)
    for i, tc in enumerate(calls, 1):
        tc["id"] = f"call_{i}"
        if on_tool_start:
            on_tool_start(tc["function"]["name"])
        if on_tool_args:
            on_tool_args(tc["function"]["name"],
                         tc["function"]["arguments"])

    usage = None
    u = result.get("usage") or {}
    if u:
        usage = {"prompt_tokens": int(u.get("inputTokens") or 0),
                 "completion_tokens": int(u.get("outputTokens") or 0)}
    stop = str(result.get("stopReason") or "end_turn")
    if "cancelled" in stop:
        from .cancelguard import TurnCancelled
        raise TurnCancelled("devin acp cancelled the prompt")
    if "refusal" in stop:
        raise APIError("devin acp refused the prompt", status=0)
    finish = "tool_calls" if calls else ("length" if "max" in stop
                                         else "stop")
    return StreamResult(content=content, reasoning="".join(think_parts),
                        tool_calls=calls, finish_reason=finish,
                        usage=usage, model=model.id)


def _tool_schemas(tools: list[dict] | None) -> dict:
    """Map tool name -> set of declared parameter names, for scoped
    loose-argument parsing."""
    out = {}
    for t in tools or []:
        fn = t.get("function") or t
        name = fn.get("name")
        if not name:
            continue
        props = ((fn.get("parameters") or {}).get("properties") or {})
        out[str(name)] = set(props)
    return out


def chat_stream(provider, model: config.Model, effort: config.Effort,
                messages: list[dict], tools: list[dict] | None,
                on_token=None, on_reasoning=None, on_tool_start=None,
                on_tool_args=None, should_cancel=None,
                timeout: float = config.DEFAULT_TIMEOUT,
                on_status=None, **_ignored) -> StreamResult:
    """Drop-in for client.chat_stream when provider.key == 'devin'."""
    return _run(provider, model, messages, tools, timeout,
                on_token, on_reasoning, on_tool_start, on_tool_args,
                should_cancel, on_status)


def chat_blocking(provider, model: config.Model, effort: config.Effort,
                  messages: list[dict], tools: list[dict] | None,
                  timeout: float = config.DEFAULT_TIMEOUT,
                  should_cancel=None, **_ignored) -> StreamResult:
    """Drop-in for client.chat_blocking (non-streaming callers)."""
    return _run(provider, model, messages, tools, timeout,
                None, None, None, None, should_cancel, None)


def is_devin(provider) -> bool:
    return getattr(provider, "key", "") == "devin"
