"""OpenAI-compatible streaming chat client (requests + manual SSE parsing)."""

from __future__ import annotations

import json
import queue
import random
import re
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterator

import requests

from . import config
from ._foundation import get_logger, NetworkError
from .config import Effort, Model, Provider

_log = get_logger("client")

RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
MAX_RETRIES = 3
# Fail fast on TCP/TLS connect; the (long) read budget stays untouched for
# slow streams. A dead provider hangs 10s here, not the full 300s timeout.
CONNECT_TIMEOUT = 10.0

class APIError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


# -- fast HTTP ---------------------------------------------------------------
# ONE pooled session for every request: the TCP+TLS handshake to the
# provider happens once and is reused for every model call, tool turn and
# follow-up. A turn that makes a dozen model calls saves a full handshake
# on each — this is the single biggest latency cut.
_SESSION: requests.Session | None = None
_SESSION_LOCK = threading.Lock()


def _http() -> requests.Session:
    global _SESSION
    # Double-checked locking: prewarm_connection runs on a background
    # thread, so two threads can otherwise build a pooled Session each and
    # silently drop one (leaking its pool/sockets until GC).
    if _SESSION is None:
        with _SESSION_LOCK:
            if _SESSION is None:
                s = requests.Session()
                adapter = requests.adapters.HTTPAdapter(
                    pool_connections=8, pool_maxsize=16, max_retries=0)
                s.mount("https://", adapter)
                s.mount("http://", adapter)
                _SESSION = s
    return _SESSION


def _timeouts(timeout: float) -> tuple[float, float]:
    """Split (connect, read) timeouts. Connect fails fast; the read budget
    stays long because a healthy stream can legitimately take minutes."""
    return (min(CONNECT_TIMEOUT, timeout), timeout)


def _backoff(attempt: int) -> float:
    """Retry delay: fast first retry, then escalate, with jitter so a fleet
    of agents doesn't thundering-herd a recovering provider."""
    base = 0.5 if attempt == 0 else 2.0 * attempt
    return base * random.uniform(0.8, 1.2)


def _base_headers(provider: Provider, stream: bool) -> dict[str, str]:
    """The per-request headers. Built from one place so every call path
    (streaming, blocking, overflow-retry) sends the identical set."""
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }
    if stream:
        headers["Accept"] = "text/event-stream"
    return headers


# -- prewarm: skip redundant warmups -----------------------------------------
# The warmup's value is the pooled TCP+TLS connection, not the /models body
# (which nobody reads). Warming the same provider twice within the TTL is a
# wasted round-trip, so remember per-base_url warm times.
_warm_at: dict[str, float] = {}
_PREWARM_TTL = 120.0


def prewarm_connection(provider) -> None:
    """SPEED: establish the TCP+TLS connection to the provider at startup
    (or model switch), so the first model call doesn't pay the handshake
    cost. Runs in a background thread — never blocks the UI. The connection
    sits in the pool ready for the first real request. Warmups for the same
    provider within the TTL are skipped (no redundant API calls)."""
    def _warm():
        key = provider.base_url
        now = time.monotonic()
        if now - _warm_at.get(key, 0.0) < _PREWARM_TTL:
            return
        _warm_at[key] = now
        try:
            url = provider.base_url.rstrip("/") + "/models"
            _http().get(url, timeout=_timeouts(5),
                        headers={"Authorization": f"Bearer {provider.api_key}"})
        except Exception:
            pass  # prewarming is best-effort, never a crash path

    threading.Thread(target=_warm, daemon=True, name="prewarm").start()


class TurnCancelled(Exception):
    """Raised inside the stream loop when the user cancels (Ctrl+C)."""


@dataclass
class ToolCallDelta:
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class StreamResult:
    content: str = ""
    reasoning: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict | None = None
    model: str = ""


def assistant_message(content: str | None, tool_calls: list[dict],
                      reasoning: str = "") -> dict:
    """Build an assistant message that is valid for thinking-aware
    backends.

    Reasoning-capable backends can validate the *history*: any
    assistant turn that carried tool_calls must also carry
    reasoning_content when the model was invoked with thinking enabled
    (reasoning_effort=low). If the history was built while reasoning
    was stripped, the next request fails with
    'messages[N].reasoning_content is required for thinking tool-call
    history'.

    This helper guarantees the invariant: tool-call turns always carry
    reasoning_content (real reasoning when available, else an empty
    string), so history never becomes invalid regardless of the
    provider's suppression mode.
    """
    msg: dict[str, Any] = {"role": "assistant"}
    # OpenAI spec: content is nullable when tool_calls are present. Preserve
    # any string the backend gave us — empty/whitespace text from a reasoning
    # model that spent the budget on thinking is a legitimate reply, not the
    # same as "no content at all". Using truthiness here was turning real
    # "" / " " replies into None and corrupting conversation history.
    msg["content"] = content if content is not None else None
    if tool_calls:
        msg["tool_calls"] = tool_calls
    # Always carry reasoning_content when reasoning exists OR when the
    # turn carried tool_calls (history validation requires it).  Set
    # both keys for maximum provider compatibility.
    if reasoning:
        msg["reasoning_content"] = reasoning
        # some providers also accept "reasoning"
        msg["reasoning"] = reasoning
    elif tool_calls:
        msg["reasoning_content"] = ""
    return msg


def _sanitize_messages(messages: list[dict]) -> bool:
    """Ensure history satisfies thinking validation.

    Mutates `messages` in place: any assistant message with tool_calls
    that lacks reasoning_content/reasoning gets an empty
    reasoning_content. Returns True if anything was fixed.

    Note: an existing empty string ("") counts as present — the
    provider only requires the field to exist, not to be non-empty.
    """
    fixed = False
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            has_rc = m.get("reasoning_content") is not None
            has_r = m.get("reasoning") is not None
            if has_rc or has_r:
                continue
            m["reasoning_content"] = ""
            fixed = True
    return fixed


def _is_reasoning_content_error(err: Exception) -> bool:
    """True if an APIError is the reasoning_content validation."""
    msg = str(err).lower()
    return "reasoning_content" in msg and "required" in msg


def build_payload(model: Model, effort: Effort, messages: list[dict],
                  tools: list[dict] | None, stream: bool = True) -> dict:
    # sanitize history before it hits the wire — fixes stale sessions that
    # were built before reasoning_content was preserved
    _sanitize_messages(messages)
    payload: dict[str, Any] = {
        "model": model.id,
        "messages": messages,
        "stream": stream,
        "temperature": effort.temperature,
    }
    if effort.max_tokens:
        # The request must fit in the window: input + max_tokens <= window,
        # otherwise the backend rejects it wholesale.
        # The input estimate is computed ONCE and shared by the clamp and
        # the hard invariant below — previously the whole conversation was
        # JSON-serialized up to 4 extra times per request.
        input_tokens = _input_tokens(model, messages, tools)
        windowed = _window_max_tokens(model, effort, messages, tools,
                                      input_tokens)
        payload["max_tokens"] = _clamp_max_tokens(model.provider, windowed)
        # HARD INVARIANT — the last mechanical gate before the wire. No
        # request may leave with estimated input + max_tokens over the
        # window. If any earlier layer drifted, clamp again here rather
        # than send a doomed request.
        window = effective_window(model)
        if input_tokens + payload["max_tokens"] > window:
            payload["max_tokens"] = max(
                _MIN_COMPLETION_TOKENS, window - input_tokens - CONTEXT_MARGIN)
    if tools and model.supports_tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    # Thinking remains disabled; Union Alpha does not advertise reasoning.
    if model.supports_reasoning:
        payload["reasoning_effort"] = "none"
    return payload


def estimate_tokens(obj: Any, model_id: str = "") -> int:
    """Deterministic token estimate for any JSON-serialisable object.

    Starts from a conservative ~3.2 chars/token baseline, then corrects
    itself with the REAL chars/token ratio learned from every backend
    response (usage.prompt_tokens vs the prompt we actually sent). The
    backend's own tokenizer is the ground truth — once a few responses
    have landed, this estimate tracks reality instead of a fixed guess,
    which is what keeps long sessions from ever overflowing the window.

    Calibration is PER MODEL (tokenizers differ between models) and is
    persisted to disk, so the very first request of a fresh process on a
    big project already uses the learned ratio."""
    try:
        payload = json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = str(obj)
    return max(1, int(len(payload) / _chars_per_token(model_id)))


# -- learned tokenizer calibration ------------------------------------------
# The backend rejects a request when input + max_tokens exceeds the model's
# context window, and it counts input with ITS OWN tokenizer. A fixed
# chars/token guess always drifts from reality (code, unicode, tool schemas
# all tokenize differently), so we learn the real ratio from every response
# and keep a safety margin on top.
#
# Enterprise hardening:
#   * calibration is keyed by model id — switching models mid-session can
#     never poison the estimate (each model has its own tokenizer);
#   * calibration AND learned context windows persist to disk, so a restart
#     on a huge project starts already calibrated;
#   * the context window itself is learned: when a backend error reports a
#     smaller window than configured, we remember it for that model.

_BASELINE_CHARS_PER_TOKEN = 3.2   # conservative start (real code is denser)
_MIN_CHARS_PER_TOKEN = 2.0        # never assume text is cheaper than this
_RATIO_SAMPLES_MAX = 8            # rolling window of recent measurements
_ratio_samples: dict[str, list[float]] = {}   # model id -> measured ratios
_learned_windows: dict[str, int] = {}         # model id -> real window
_calibration_loaded = False
_CALIBRATION_FILE = config.APP_DIR / "calibration.json"


def _cal_key(model_id: str) -> str:
    """Calibration bucket key. Unknown/empty model ids share one bucket."""
    return model_id or "_default"


def _load_calibration() -> None:
    """Restore persisted calibration once per process (best effort)."""
    global _calibration_loaded
    if _calibration_loaded:
        return
    _calibration_loaded = True
    try:
        data = json.loads(_CALIBRATION_FILE.read_text())
    except (OSError, ValueError):
        return
    ratios = data.get("ratios") or {}
    for key, samples in ratios.items():
        if isinstance(samples, list):
            clean = [float(s) for s in samples
                     if isinstance(s, (int, float))
                     and _MIN_CHARS_PER_TOKEN * 0.5 <= float(s) <= 16.0]
            if clean:
                _ratio_samples[key] = clean[-_RATIO_SAMPLES_MAX:]
    windows = data.get("windows") or {}
    for key, win in windows.items():
        if isinstance(win, int) and win > 0:
            _learned_windows[key] = win


def _save_calibration() -> None:
    """Persist calibration so the next process starts already tuned."""
    try:
        config.ensure_dirs()
        # Atomic write (tmp + rename): concurrent savers from worker
        # threads can otherwise interleave and tear the JSON, silently
        # losing all calibration on the next load.
        tmp = _CALIBRATION_FILE.with_name(_CALIBRATION_FILE.name + ".tmp")
        tmp.write_text(json.dumps({
            "ratios": _ratio_samples,
            "windows": _learned_windows,
        }))
        tmp.replace(_CALIBRATION_FILE)
    except OSError:
        pass  # persistence is an optimisation, never a failure path


def _chars_per_token(model_id: str = "") -> float:
    """Current best chars/token for this model: the mean of its recent
    real measurements when we have any, else the conservative baseline."""
    _load_calibration()
    samples = _ratio_samples.get(_cal_key(model_id)) \
        or _ratio_samples.get("_default")
    if not samples:
        return _BASELINE_CHARS_PER_TOKEN
    return sum(samples) / len(samples)


def learn_token_ratio(sent_chars: int, actual_tokens: int,
                      model_id: str = "") -> None:
    """Record one real (chars, tokens) measurement from a backend response.
    Called after every completion whose usage reports prompt_tokens."""
    if sent_chars <= 0 or actual_tokens <= 0:
        return
    ratio = sent_chars / actual_tokens
    # ignore implausible outliers (broken usage reporting)
    if not (_MIN_CHARS_PER_TOKEN * 0.5 <= ratio <= 16.0):
        return
    key = _cal_key(model_id)
    samples = _ratio_samples.setdefault(key, [])
    samples.append(ratio)
    if len(samples) > _RATIO_SAMPLES_MAX:
        del samples[: len(samples) - _RATIO_SAMPLES_MAX]
    _save_calibration()


def learn_context_window(model_id: str, window: int) -> None:
    """Remember the real context window a backend reported for a model.
    We only ever shrink the configured window — a backend that reports a
    smaller window is authoritative for that deployment."""
    if window <= 0 or not model_id:
        return
    key = _cal_key(model_id)
    known = _learned_windows.get(key)
    if known is None or window < known:
        _learned_windows[key] = window
        _save_calibration()


def effective_window(model: Model) -> int:
    """The context window to plan against: the configured value, capped by
    anything the backend has actually reported for this model."""
    _load_calibration()
    learned = _learned_windows.get(_cal_key(model.id))
    if learned is None:
        return model.context_window
    return min(model.context_window, learned)


def _prompt_chars(messages: list[dict], tools: list[dict] | None) -> int:
    """Character count of exactly what we send as the prompt."""
    try:
        payload = json.dumps({"messages": messages, "tools": tools or []},
                             ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = str(messages) + str(tools or [])
    return len(payload)


# Safety headroom between the estimated input and the window. Scales with
# input size: the bigger the prompt, the bigger the absolute tokenizer
# error can be, so the margin grows instead of staying a fixed constant.
CONTEXT_MARGIN = 8_192
_MIN_COMPLETION_TOKENS = 1_024    # never clamp max_tokens below this


def _input_tokens(model: Model, messages: list[dict],
                  tools: list[dict] | None) -> int:
    """Estimated prompt tokens for the exact request about to be sent.
    Computed once per attempt and shared by the clamp and the hard
    invariant, so the conversation isn't re-serialized for every check."""
    n = estimate_tokens(messages, model.id)
    if tools and model.supports_tools:
        n += estimate_tokens(tools, model.id)
    return n


def _window_max_tokens(model: Model, effort: Effort, messages: list[dict],
                       tools: list[dict] | None,
                       input_tokens: int | None = None) -> int:
    """Clamp the requested max_tokens so that input + max_tokens fits in
    the model's context window. Backends reject the whole request when
    the sum exceeds the window (e.g. 'maximum context length of 262144
    tokens'). The input size is estimated with the learned per-model
    chars/token ratio (see estimate_tokens) plus a size-scaled margin, so
    the clamp stays correct even as the conversation grows for hours. If
    the input alone overflows the window, raise a clear, recoverable
    error instead of sending a doomed request."""
    requested = effort.max_tokens or 0
    if not requested:
        return 0
    window = effective_window(model)
    if input_tokens is None:
        input_tokens = _input_tokens(model, messages, tools)
    # margin grows with the prompt: ~1 extra token of headroom per 32
    # estimated input tokens, on top of the fixed floor
    margin = CONTEXT_MARGIN + input_tokens // 32
    if input_tokens + margin + _MIN_COMPLETION_TOKENS > window:
        raise APIError(
            f"conversation is too large for {model.label} "
            f"(~{input_tokens:,} input tokens vs {window:,} "
            f"context window) — start a new session (/new), rewind "
            f"(/rewind), or switch to a larger-context model (Ctrl+T)")
    headroom = window - input_tokens - margin
    return max(_MIN_COMPLETION_TOKENS, min(requested, headroom))


# FullAgent asks for 200k output tokens everywhere, but each backend has its
# own hard ceiling. Clamp at send time so the request is never rejected for
# an oversized max_tokens; providers without a known cap pass through.
_MAX_TOKENS_CAP: dict[str, int] = {"kilo": 131_072}


def _clamp_max_tokens(provider_key: str, value: int) -> int:
    cap = _MAX_TOKENS_CAP.get(provider_key)
    if cap is None:
        return value
    return min(value, cap)


def _iter_sse_events(resp: requests.Response) -> Iterator[dict]:
    """Yield parsed JSON data objects from an SSE stream.

    Proper SSE framing: an event ends at a blank line, and multiple
    `data:` lines inside one event are joined with newline before the
    JSON parse (the old code treated every `data:` line as a complete
    event, so a multi-line data field was split into fragments that
    each failed to parse and were silently dropped). Lines are decoded
    from bytes with errors replaced, so one corrupt chunk can never
    crash the turn with a raw UnicodeDecodeError.
    """
    data_lines: list[str] = []

    def _dispatch() -> Any:
        """Parse the accumulated event. Returns "done", a parsed object,
        or None when there is nothing (or nothing parseable) to emit."""
        text = "\n".join(data_lines).strip()
        del data_lines[:]
        if not text:
            return None
        if text == "[DONE]":
            return "done"
        try:
            return json.loads(text)
        except ValueError:
            return None

    for raw in resp.iter_lines():
        line = raw.decode("utf-8", errors="replace")
        if line.startswith("\ufeff"):
            line = line.lstrip("\ufeff")
        if not line.strip():
            # blank line: end of event — dispatch what accumulated
            ev = _dispatch()
            if ev == "done":
                return
            if ev is not None:
                yield ev
            continue
        if line.startswith(":"):  # comment / keepalive
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
    # stream ended without a trailing blank line — flush the last event
    ev = _dispatch()
    if ev is not None and ev != "done":
        yield ev


def _iter_sse_events_cancellable(
        resp: requests.Response,
        should_cancel: Callable[[], bool] | None) -> Iterator[dict]:
    """Yield SSE events, honouring should_cancel even while the provider
    stalls. A stalled stream blocks inside iter_lines until the (long)
    read timeout — without this, Esc/Ctrl+C does nothing for up to
    DEFAULT_TIMEOUT (300s) while no chunks arrive. A daemon producer
    thread keeps consuming; this thread polls with a short timeout and
    checks cancellation between polls. Producer exceptions (e.g.
    ChunkedEncodingError on a mid-stream disconnect) are re-raised here
    with their original type so the retry layer still sees them."""
    q: queue.Queue = queue.Queue()
    _END = object()

    def _produce() -> None:
        try:
            for event in _iter_sse_events(resp):
                q.put(event)
        except Exception as e:  # noqa: BLE001 — re-raised in the consumer
            q.put(e)
        finally:
            q.put(_END)

    thread = threading.Thread(target=_produce, daemon=True,
                              name="sse-producer")
    thread.start()
    while True:
        try:
            item = q.get(timeout=0.25)
        except queue.Empty:
            if should_cancel is not None and should_cancel():
                # _chat_stream_once's finally closes resp, which unblocks
                # the producer's socket read; the daemon thread then exits
                # on its own.
                raise TurnCancelled()
            continue
        if item is _END:
            return
        if isinstance(item, Exception):
            raise item
        yield item


def _extract_error_message(body: str) -> str:
    try:
        obj = json.loads(body)
    except ValueError:
        return body[:500]
    if not isinstance(obj, dict):
        # valid JSON but not an object (array/string/number) — no shape to dig
        return body[:500]
    err = obj.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err)
    if isinstance(err, str):
        return err
    return body[:500]


# -- context-overflow detection + self-healing ------------------------------
# Backends reject a request outright when input + max_tokens exceeds the
# context window, and the error message carries the REAL token counts
# (e.g. "maximum context length of 262144 tokens ... 67440 tokens from the
# input messages and 195680 tokens for the completion"). We parse those
# numbers, learn the true chars/token ratio from them, re-clamp max_tokens
# to the actual headroom, and retry — so a long session heals itself
# instead of dying with the error.

_OVERFLOW_MARKERS = ("context length", "context_length", "context window",
                     "too many tokens", "maximum context",
                     "reduce the number of tokens")


def is_context_overflow(message: str) -> bool:
    """True when an API error message is a context-window overflow."""
    low = message.lower()
    return any(m in low for m in _OVERFLOW_MARKERS)


def _parse_overflow(message: str) -> dict | None:
    """Extract real token counts from a backend overflow error. Returns a
    dict with any of: window, input_tokens, completion_tokens, total."""
    if not is_context_overflow(message):
        return None
    low = message.lower()
    info: dict[str, int] = {}

    def _num(pattern: str) -> int | None:
        m = re.search(pattern, low)
        return int(m.group(1).replace(",", "")) if m else None

    window = (_num(r"maximum context length (?:of|is)\s+([\d,]+)\s+tokens")
              or _num(r"context (?:length|window) (?:of|is)\s+([\d,]+)\s+tokens"))
    if window:
        info["window"] = window
    inp = (_num(r"([\d,]+)\s+tokens?\s+from the input")
           or _num(r"([\d,]+)\s+tokens?\s+(?:in|from)\s+(?:the\s+)?(?:input|prompt|messages)")
           or _num(r"(?:input|prompt|messages)\s+(?:resulted in|is|are)\s+([\d,]+)\s+tokens"))
    if inp:
        info["input_tokens"] = inp
    comp = _num(r"([\d,]+)\s+tokens?\s+for (?:the )?completion")
    if comp:
        info["completion_tokens"] = comp
    total = (_num(r"a total of\s+([\d,]+)\s+tokens")
             or _num(r"requested (?:a total of )?([\d,]+)\s+tokens"))
    if total:
        info["total"] = total
    return info or {}


def _fit_max_tokens_from_actual(model: Model, info: dict,
                                effort: Effort) -> int | None:
    """max_tokens that provably fits, computed from the backend's OWN
    counts. None means the input alone overflows — the caller must shrink
    the conversation, not the completion budget."""
    if info.get("window"):
        learn_context_window(model.id, info["window"])
    window = effective_window(model)
    actual_input = info.get("input_tokens")
    if not actual_input:
        total, comp = info.get("total"), info.get("completion_tokens")
        if total and comp:
            actual_input = total - comp
    if not actual_input:
        return None
    margin = max(4_096, window // 64)
    headroom = window - actual_input - margin
    if headroom < _MIN_COMPLETION_TOKENS:
        return None
    requested = effort.max_tokens or headroom
    return min(requested, headroom)


def _learn_from_usage(usage: dict | None, sent_chars: int,
                     model_id: str = "") -> None:
    """Calibrate the chars/token estimator with the backend's real count."""
    if not usage:
        return
    try:
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
    except (TypeError, ValueError):
        return
    if prompt_tokens > 0 and sent_chars > 0:
        learn_token_ratio(sent_chars, prompt_tokens, model_id)


def shrink_tool_outputs(messages: list[dict], keep: int = 1,
                        max_chars: int = 400) -> bool:
    """In-place overflow shrinker for any message list. Used as the
    on_overflow callback by sub-agents (scouts, workers) whose own tool
    loop can bloat their context — the same protection the main agent
    gets. Three escalating passes, all pairing-safe:
      1. truncate the OLDEST tool results to short summaries (keeping the
         newest `keep` verbatim),
      2. if nothing was stale, truncate the newest tool results too,
      3. if still nothing shrank, drop the oldest complete turn unit
         (user message + its assistant reply + tool results) so
         tool_call / tool-response pairing is never broken.
    Returns True if anything actually shrank."""
    tool_idx = [i for i, m in enumerate(messages)
                if m.get("role") == "tool"]
    stale = tool_idx if keep <= 0 else tool_idx[:-keep]

    def _truncate(indices: list[int]) -> bool:
        shrank = False
        for i in indices:
            content = str(messages[i].get("content") or "")
            if len(content) > max_chars:
                messages[i]["content"] = (
                    content[:max_chars]
                    + f"\n[… truncated — {len(content):,} chars originally]")
                shrank = True
        return shrank

    if _truncate(stale):
        return True
    if _truncate(tool_idx):          # even the newest results, if desperate
        return True

    # drop the oldest complete turn unit (never a lone message)
    start = 1 if messages and messages[0].get("role") == "system" else 0
    end = None
    for j in range(start + 1, len(messages)):
        if messages[j].get("role") == "user":
            end = j
            break
    if end is not None and end > start:
        del messages[start:end]
        return True
    return False


def _check_api_key(provider: Provider) -> None:
    """Fail fast with actionable guidance instead of an opaque 401."""
    if not provider.api_key:
        env_name = f"{provider.key.upper()}_API_KEY"
        raise APIError(
            f"no API key configured for {provider.name}. Set the "
            f"{env_name} environment variable or save the key in "
            f"{config.APP_DIR / (provider.key + '_api_key')} and restart.", status=401)


def chat_stream(provider: Provider, model: Model, effort: Effort,
                messages: list[dict], tools: list[dict] | None,
                on_token: Callable[[str], None] | None = None,
                on_reasoning: Callable[[str], None] | None = None,
                on_tool_start: Callable[[str], None] | None = None,
                on_tool_args: Callable[[str, str], None] | None = None,
                should_cancel: Callable[[], bool] | None = None,
                on_overflow: Callable[[], bool] | None = None,
                timeout: float = config.DEFAULT_TIMEOUT) -> StreamResult:
    """Send a streaming chat completion request; calls callbacks as tokens
    arrive; returns the fully accumulated result.

    Context-overflow recovery — three escalating layers, so a long session
    on a huge project never dies with a context-length error:
      1. pre-flight clamp with the learned per-model chars/token ratio;
      2. on rejection, parse the backend's REAL token counts, recalibrate,
         re-clamp max_tokens, retry (up to OVERFLOW_RETRIES times);
      3. if the input itself no longer fits, invoke on_overflow (the
         caller shrinks the conversation — e.g. emergency compaction) and
         retry with the shrunken messages.
    The payload invariant is re-asserted before every send: the request
    that goes out always satisfies input + max_tokens <= window."""
    _check_api_key(provider)
    url = provider.base_url.rstrip("/") + "/chat/completions"
    headers = _base_headers(provider, stream=True)
    current_effort = effort
    shrinks_used = 0

    # Tracks whether ANY output reached the UI during the current attempt.
    # A retry replays the WHOLE request — once tokens/tool-args have been
    # shown, retrying would duplicate the completion on screen, so
    # post-output failures surface immediately instead of being retried.
    streamed = {"out": False}

    def _mark_emitted(cb):
        if cb is None:
            return None

        def _wrapped(piece: str) -> None:
            streamed["out"] = True
            cb(piece)

        return _wrapped

    def _mark_emitted_args(cb):
        if cb is None:
            return None

        def _wrapped(name: str, chunk: str) -> None:
            streamed["out"] = True
            cb(name, chunk)

        return _wrapped

    on_token_w = _mark_emitted(on_token)
    on_reasoning_w = _mark_emitted(on_reasoning)
    on_tool_args_w = _mark_emitted_args(on_tool_args)

    for overflow_attempt in range(OVERFLOW_RETRIES + OVERFLOW_SHRINKS + 1):
        sent_chars = _prompt_chars(messages, tools)
        try:
            payload = build_payload(model, current_effort, messages, tools,
                                    stream=True)
        except APIError as e:
            # pre-flight refusal (input already over the window). Give the
            # caller a chance to shrink the conversation and retry.
            if (is_context_overflow(str(e)) and on_overflow is not None
                    and shrinks_used < OVERFLOW_SHRINKS and on_overflow()):
                shrinks_used += 1
                continue
            raise
        try:
            result = _chat_stream_with_retries(
                url, headers, payload, on_token_w, on_reasoning_w,
                on_tool_start, on_tool_args_w, should_cancel, timeout)
            _learn_from_usage(result.usage, sent_chars, model.id)
            return result
        except APIError as e:
            # reasoning_content validation — heal history and retry once,
            # but NEVER after output already streamed (see `streamed`)
            if _is_reasoning_content_error(e):
                if streamed["out"]:
                    raise
                if _sanitize_messages(messages):
                    continue
                # already sanitized but provider still rejects — try
                # stripping reasoning entirely and retry with sanitized copy
                # (some providers accept empty string, some need removal)
                # we already sanitized, so just raise with clearer guidance
                raise APIError(
                    f"{e} — history was sanitized but provider still "
                    f"rejects. Try /new or /rewind to clear the stale "
                    f"thinking turn.") from e
            if streamed["out"]:
                # _chat_stream_with_retries only raises post-output for
                # non-retryable failures; healing or shrinking now would
                # replay the request on top of the partial output
                raise
            healed = _heal_overflow(model, current_effort, e,
                                    overflow_attempt, sent_chars)
            if healed is not None:
                current_effort = healed
                continue
            # clamping cannot help — the input itself is too big. Let the
            # caller shrink the conversation, then retry.
            if (is_context_overflow(str(e)) and on_overflow is not None
                    and shrinks_used < OVERFLOW_SHRINKS and on_overflow()):
                shrinks_used += 1
                continue
            raise

    # retries exhausted — surface the last overflow error
    raise APIError(
        f"conversation is too large for {model.label} even after "
        f"re-clamping — start a new session (/new) or rewind (/rewind)")


OVERFLOW_RETRIES = 2   # re-clamp-and-retry attempts (input still fits)
OVERFLOW_SHRINKS = 2   # shrink-the-conversation-and-retry attempts


def _heal_overflow(model: Model, effort: Effort, error: APIError,
                   attempt: int, sent_chars: int = 0) -> Effort | None:
    """Turn a backend overflow error into a tighter Effort, or None when
    the input itself no longer fits (nothing a clamp can fix)."""
    if not is_context_overflow(str(error)):
        return None
    if attempt >= OVERFLOW_RETRIES:
        return None
    info = _parse_overflow(str(error)) or {}
    # the backend just told us the real input size — calibrate the
    # per-model estimator with it so every later clamp is accurate
    if info.get("input_tokens") and sent_chars > 0:
        learn_token_ratio(sent_chars, info["input_tokens"], model.id)
    fitted = _fit_max_tokens_from_actual(model, info, effort)
    if fitted is None:
        return None
    if effort.max_tokens is not None and fitted >= effort.max_tokens:
        return None  # no tightening left to do
    return replace(effort, max_tokens=fitted)


def _chat_stream_with_retries(
        url: str, headers: dict, payload: dict,
        on_token: Callable[[str], None] | None,
        on_reasoning: Callable[[str], None] | None,
        on_tool_start: Callable[[str], None] | None,
        on_tool_args: Callable[[str, str], None] | None,
        should_cancel: Callable[[], bool] | None,
        timeout: float) -> StreamResult:
    """The plain retry loop (rate limits, timeouts, connection errors).

    A retry restarts the WHOLE request — once any token has already been
    streamed to the UI a restart would replay (duplicate) the completion on
    top of the partial output, so mid-output failures surface immediately
    instead of being retried."""
    last_error: Exception | None = None
    attempt = 0
    emitted = {"out": False}

    def _tok(piece: str) -> None:
        emitted["out"] = True
        if on_token:
            on_token(piece)

    def _reason(piece: str) -> None:
        emitted["out"] = True
        if on_reasoning:
            on_reasoning(piece)

    def _targs(name: str, chunk: str) -> None:
        # tool-call arguments are already flowing to the UI — a retry now
        # would replay them on top of the live write, so mark as emitted
        emitted["out"] = True
        if on_tool_args:
            on_tool_args(name, chunk)

    while attempt < MAX_RETRIES:
        try:
            return _chat_stream_once(url, headers, payload,
                                     _tok, _reason, on_tool_start,
                                     _targs, should_cancel, timeout)
        except TurnCancelled:
            raise
        except APIError as e:
            last_error = e
            if e.status in RETRY_STATUSES and attempt < MAX_RETRIES - 1:
                # fast first retry, then escalate — rate limits resolve
                # quickly on free tiers; never stall the UI for seconds
                time.sleep(_backoff(attempt))
                attempt += 1
                continue
            raise
        except requests.exceptions.Timeout as e:
            last_error = e
            if attempt < MAX_RETRIES - 1 and not emitted["out"]:
                time.sleep(_backoff(attempt))
                attempt += 1
                continue
            if emitted["out"]:
                raise APIError(
                    f"stream stalled: no data for {timeout:g}s after output "
                    f"began — /retry to resend") from e
            raise APIError(f"request timed out after {timeout:g}s") from e
        except (requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError) as e:
            # ChunkedEncodingError is the typical MID-STREAM disconnect
            # (urllib3 ProtocolError / IncompleteRead) — without catching
            # it here it bypasses the retry budget entirely
            last_error = e
            if attempt < MAX_RETRIES - 1 and not emitted["out"]:
                time.sleep(_backoff(attempt))
                attempt += 1
                continue
            raise APIError(f"connection failed: {e}") from e
        except requests.exceptions.RequestException as e:
            # Anything else requests can raise (TooManyRedirects,
            # InvalidURL, ...): not retryable, but surface it as a clean
            # APIError instead of a raw requests exception.
            raise APIError(f"request failed: {e}") from e
    raise APIError(str(last_error))


def _sse_error_status(err: Any) -> int | None:
    """Best-effort HTTP status for an SSE-embedded error object, so the
    retry layer can treat a streamed 429 like a real 429 (and never retry
    a streamed 401). OpenAI-style error objects carry a numeric `code`;
    some gateways send it as a string or a "rate_limit_exceeded" slug."""
    if not isinstance(err, dict):
        return None
    code = err.get("code")
    if code is None:
        code = err.get("status")
    if isinstance(code, bool):
        return None
    if isinstance(code, int):
        return code if 100 <= code < 600 else None
    if isinstance(code, str):
        low = code.strip().lower()
        if low.isdigit():
            n = int(low)
            return n if 100 <= n < 600 else None
        if "rate" in low and "limit" in low:
            return 429
    return None


def _chat_stream_once(url: str, headers: dict, payload: dict,
                      on_token: Callable[[str], None] | None,
                      on_reasoning: Callable[[str], None] | None,
                      on_tool_start: Callable[[str], None] | None,
                      on_tool_args: Callable[[str, str], None] | None,
                      should_cancel: Callable[[], bool] | None,
                      timeout: float) -> StreamResult:
    result = StreamResult()
    tc_acc: dict[int, ToolCallDelta] = {}
    announced_tools: set[int] = set()

    resp = _http().post(url, headers=headers, json=payload,
                        stream=True, timeout=_timeouts(timeout))
    if resp.status_code != 200:
        body = resp.text
        resp.close()  # release the pooled connection on the error path too
        raise APIError(_extract_error_message(body), status=resp.status_code)

    try:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "text/event-stream" not in ctype:
            # Some providers return a non-streamed JSON body even when
            # stream=true was requested — parse it like blocking mode
            # instead of silently dropping the whole completion.
            try:
                data = resp.json()
            except ValueError as e:
                raise APIError(
                    f"provider returned invalid JSON "
                    f"(status {resp.status_code}): {str(e)[:200]}",
                    status=resp.status_code) from e
            # NB: the isinstance check must happen BEFORE data.get — a
            # provider returning a JSON array/string here used to escape
            # as a raw AttributeError instead of a clean APIError.
            model_id = data.get("model") if isinstance(data, dict) else None
            return _result_from_json(data, str(model_id or ""))
        events = (_iter_sse_events_cancellable(resp, should_cancel)
                  if should_cancel is not None
                  else _iter_sse_events(resp))
        for event in events:
            if should_cancel is not None and should_cancel():
                raise TurnCancelled()
            if not isinstance(event, dict):
                continue
            if event.get("model"):
                result.model = event["model"]
            if event.get("usage"):
                result.usage = event["usage"]
            if event.get("error"):
                err = event["error"]
                msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                raise APIError(msg, status=_sse_error_status(err))
            choices = event.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta") or {}

            if choice.get("finish_reason"):
                result.finish_reason = choice["finish_reason"]

            piece = delta.get("content")
            if piece:
                result.content += piece
                if on_token:
                    on_token(piece)

            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if reasoning:
                result.reasoning += reasoning
                if on_reasoning:
                    on_reasoning(reasoning)

            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                acc = tc_acc.setdefault(idx, ToolCallDelta())
                if tc.get("id"):
                    acc.id = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    acc.name += fn["name"]
                    if idx not in announced_tools and on_tool_start:
                        announced_tools.add(idx)
                        on_tool_start(acc.name)
                if fn.get("arguments"):
                    acc.arguments += fn["arguments"]
                    if on_tool_args:
                        on_tool_args(acc.name, fn["arguments"])
    finally:
        resp.close()

    for idx in sorted(tc_acc):
        acc = tc_acc[idx]
        result.tool_calls.append({
            "id": acc.id or f"call_{idx}",
            "type": "function",
            "function": {"name": acc.name, "arguments": acc.arguments},
        })

    return result


def _result_from_json(data: dict, model_id: str) -> StreamResult:
    """Parse a non-streamed chat.completion JSON body into a StreamResult
    (shared by blocking mode and the stream-mode JSON fallback)."""
    # Providers occasionally return a JSON array/string instead of the
    # chat.completion object — without this guard `data.get` raises a raw
    # AttributeError that bypasses every error handler upstream.
    if not isinstance(data, dict):
        raise APIError(
            f"provider returned unexpected JSON shape "
            f"({type(data).__name__}): {str(data)[:200]}")
    result = StreamResult(model=data.get("model", model_id),
                          usage=data.get("usage"))
    choices = data.get("choices") or []
    if choices:
        msg = choices[0].get("message") or {}
        result.content = msg.get("content") or ""
        result.reasoning = (msg.get("reasoning_content")
                            or msg.get("reasoning") or "")
        result.finish_reason = choices[0].get("finish_reason")
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            result.tool_calls.append({
                "id": tc.get("id", "call_0"),
                "type": "function",
                "function": {"name": fn.get("name", ""),
                             "arguments": fn.get("arguments", "")},
            })
    return result


def _post_blocking(url: str, headers: dict, payload: dict,
                   timeout: float) -> dict:
    """POST with the same retry policy as the streaming path (rate limits,
    timeouts, connection errors). Blocking calls have no partial output,
    so every attempt is replay-safe — unlike the stream path there is no
    emitted-output guard here.

    The response is ALWAYS closed (try/finally): leaking it would pin a
    pooled connection and eventually starve the session pool."""
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = _http().post(url, headers=headers, json=payload,
                                timeout=_timeouts(timeout))
        except requests.exceptions.Timeout as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(_backoff(attempt))
                continue
            raise APIError(f"request timed out after {timeout:g}s") from e
        except (requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError) as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(_backoff(attempt))
                continue
            raise APIError(f"connection failed: {e}") from e
        except requests.exceptions.RequestException as e:
            # Same contract as the streaming path: anything else requests
            # can raise (TooManyRedirects, InvalidURL, InvalidHeader, ...)
            # surfaces as a clean APIError, never a raw requests
            # exception.
            raise APIError(f"request failed: {e}") from e
        try:
            if resp.status_code != 200:
                raise APIError(_extract_error_message(resp.text),
                               status=resp.status_code)
            try:
                data = resp.json()
            except ValueError as e:
                raise APIError(
                    f"provider returned invalid JSON "
                    f"(status {resp.status_code}): {str(e)[:200]}",
                    status=resp.status_code) from e
            if not isinstance(data, dict):
                raise APIError(
                    f"provider returned unexpected JSON shape "
                    f"({type(data).__name__}): {str(data)[:200]}",
                    status=resp.status_code)
            return data
        except APIError as e:
            if e.status in RETRY_STATUSES and attempt < MAX_RETRIES - 1:
                time.sleep(_backoff(attempt))
                continue
            raise
        finally:
            resp.close()
    raise APIError(str(last_error))  # unreachable, keeps type checkers calm


def chat_blocking(provider: Provider, model: Model, effort: Effort,
                  messages: list[dict], tools: list[dict] | None,
                  on_overflow: Callable[[], bool] | None = None,
                  timeout: float = config.DEFAULT_TIMEOUT) -> StreamResult:
    """Non-streaming fallback (used when a provider rejects stream=true).

    Carries the same three-layer context-overflow recovery as chat_stream:
    pre-flight clamp, re-clamp-and-retry on rejection, and (when the input
    itself is too big) caller-driven shrink-and-retry via on_overflow."""
    _check_api_key(provider)
    url = provider.base_url.rstrip("/") + "/chat/completions"
    headers = _base_headers(provider, stream=False)
    current_effort = effort
    shrinks_used = 0

    for overflow_attempt in range(OVERFLOW_RETRIES + OVERFLOW_SHRINKS + 1):
        sent_chars = _prompt_chars(messages, tools)
        try:
            payload = build_payload(model, current_effort, messages, tools,
                                    stream=False)
        except APIError as e:
            # pre-flight refusal (input already over the window). Give the
            # caller a chance to shrink the conversation and retry.
            if (is_context_overflow(str(e)) and on_overflow is not None
                    and shrinks_used < OVERFLOW_SHRINKS and on_overflow()):
                shrinks_used += 1
                continue
            raise
        try:
            data = _post_blocking(url, headers, payload, timeout)
        except APIError as err:
            if _is_reasoning_content_error(err):
                if _sanitize_messages(messages):
                    continue
                raise APIError(
                    f"{err} — history was sanitized but provider still "
                    f"rejects. Try /new or /rewind.") from err
            healed = _heal_overflow(model, current_effort, err,
                                    overflow_attempt, sent_chars)
            if healed is not None:
                current_effort = healed
                continue
            if (is_context_overflow(str(err)) and on_overflow is not None
                    and shrinks_used < OVERFLOW_SHRINKS and on_overflow()):
                shrinks_used += 1
                continue
            raise
        result = _result_from_json(data, model.id)
        _learn_from_usage(result.usage, sent_chars, model.id)
        return result

    raise APIError(
        f"conversation is too large for {model.label} even after "
        f"re-clamping — start a new session (/new) or rewind (/rewind)")
