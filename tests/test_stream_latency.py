"""Streaming first-token / incremental-delivery proof tests.

The user reported 512+ second responses and suspected tokens were being
buffered instead of displayed immediately. These tests drive the REAL
stream pipeline (`_iter_sse_events` -> `_chat_stream_once` -> on_token
callback) with a fake SSE source that dribbles tokens out slowly, and
prove the consumer is notified per token — not in one batch at the end
of the stream.

Timing bounds are deliberately generous so the tests are stable on
loaded CI machines; a batching implementation would miss them by an
order of magnitude.
"""
import json
import os
import sys
import time
import unittest

# allow `python tests/test_stream_latency.py` from the repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from unittest.mock import patch

from fullagent import client
from fullagent.client import _chat_stream_once, _iter_sse_events


def _event_line(payload: dict) -> bytes:
    return ("data: " + json.dumps(payload)).encode("utf-8")


def _content_event(text: str) -> dict:
    return {"choices": [{"delta": {"content": text}}]}


class FakeSlowResponse:
    """A stand-in for requests.Response whose iter_lines() yields one
    SSE event at a time, sleeping `gap` seconds before each event —
    like a provider generating tokens slowly."""

    def __init__(self, events, gap: float = 0.05):
        self.status_code = 200
        self.headers = {"Content-Type": "text/event-stream"}
        self._events = events
        self._gap = gap
        self.closed = False

    def iter_lines(self):
        for ev in self._events:
            if self._gap > 0:
                # sleep(0) is NOT free: it forces a scheduler/GIL handoff
                # on every event and would dominate a 20k-chunk perf test.
                time.sleep(self._gap)
            yield _event_line(ev)
            yield b""  # SSE event terminator (blank line)
        if self._gap > 0:
            time.sleep(self._gap)
        yield b"data: [DONE]"
        yield b""

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, resp):
        self._resp = resp

    def post(self, *args, **kwargs):
        return self._resp


N_TOKENS = 10
GAP = 0.05  # seconds between token events; total stream ~= 0.55s


def _run_stream():
    """Drive _chat_stream_once with a slow fake SSE stream. Returns
    (result, [(piece, timestamp)], start_time, end_time)."""
    tokens = [f"tok{i} " for i in range(N_TOKENS)]
    resp = FakeSlowResponse([_content_event(t) for t in tokens], gap=GAP)
    seen: list[tuple[str, float]] = []
    start = time.monotonic()
    with patch.object(client, "_http", return_value=FakeSession(resp)):
        result = _chat_stream_once(
            "http://fake/v1/chat/completions", {}, {},
            on_token=lambda p: seen.append((p, time.monotonic())),
            on_reasoning=None, on_tool_start=None, on_tool_args=None,
            should_cancel=None, timeout=30.0)
    end = time.monotonic()
    return result, seen, start, end, tokens


class IncrementalDeliveryTests(unittest.TestCase):
    def test_sse_events_arrive_incrementally_not_batched(self):
        """The SSE parser must emit each event as its blank-line
        terminator arrives, not buffer everything until [DONE]."""
        events = [_content_event(f"t{i}") for i in range(6)]
        resp = FakeSlowResponse(events, gap=GAP)
        stamps = []
        t0 = time.monotonic()
        for ev in _iter_sse_events(resp):
            stamps.append(time.monotonic() - t0)
            self.assertEqual(ev["choices"][0]["delta"]["content"],
                             f"t{len(stamps) - 1}")
        # 6 events x 50ms gaps: first must arrive quickly...
        self.assertLess(stamps[0], 0.30,
                        f"first SSE event took {stamps[0]:.2f}s — buffered?")
        # ...and arrivals must be spread across the stream, not bunched
        # at the end (a batching parser would show ~0 spread).
        self.assertGreaterEqual(stamps[-1] - stamps[0], 0.15)

    def test_on_token_fires_per_token_without_waiting_for_stream_end(self):
        """Core first-token proof: the display callback fires for each
        token as it arrives over a slow stream."""
        result, seen, start, end, tokens = _run_stream()
        self.assertEqual(len(seen), N_TOKENS)
        first_delay = seen[0][1] - start
        stream_len = end - start
        print(f"\n  stream duration: {stream_len:.2f}s, "
              f"first on_token after: {first_delay:.2f}s")
        # The first token must surface long before the stream finishes —
        # a batching pipeline would deliver it only at stream end.
        self.assertLess(first_delay, stream_len * 0.6,
                        f"first token delayed {first_delay:.2f}s into a "
                        f"{stream_len:.2f}s stream — tokens are buffered")
        # Callbacks must be spread across the stream: with 10 tokens at
        # 50ms gaps the span is ~0.45s; batching would give ~0.
        span = seen[-1][1] - seen[0][1]
        print(f"  callback span first->last: {span:.2f}s "
              f"(expected ~{(N_TOKENS - 1) * GAP:.2f}s)")
        self.assertGreaterEqual(span, (N_TOKENS - 1) * GAP * 0.5)
        # And the accumulated result must still be complete.
        self.assertEqual(result.content, "".join(tokens))

    def _time_accumulation(self, n: int, kind: str) -> tuple[float, str]:
        """Run _chat_stream_once over n chunks; return (seconds, text)."""
        if kind == "content":
            chunks = [f"w{i} " for i in range(n)]
            events = [_content_event(c) for c in chunks]
        else:
            chunks = [f"arg{i:05d};" for i in range(n)]
            events = []
            for i, c in enumerate(chunks):
                delta = {"tool_calls": [{"index": 0,
                                         "function": {"arguments": c}}]}
                if i == 0:
                    delta["tool_calls"][0]["id"] = "call_0"
                    delta["tool_calls"][0]["function"]["name"] = "write_file"
                events.append({"choices": [{"delta": delta}]})
        resp = FakeSlowResponse(events, gap=0.0)
        t0 = time.monotonic()
        with patch.object(client, "_http", return_value=FakeSession(resp)):
            result = _chat_stream_once(
                "http://fake/v1/chat/completions", {}, {},
                on_token=None, on_reasoning=None, on_tool_start=None,
                on_tool_args=None, should_cancel=None, timeout=30.0)
        if kind == "content":
            text = result.content
        else:
            text = result.tool_calls[0]["function"]["arguments"]
            self.assertEqual(result.tool_calls[0]["function"]["name"],
                             "write_file")
        self.assertEqual(text, "".join(chunks))
        return time.monotonic() - t0, text

    def test_accumulation_scales_linearly_not_quadratically(self):
        """Regression guard for the O(n^2) `+=` hot loops (content,
        reasoning, tool arguments): quadrupling the chunk count must
        roughly quadruple the time (linear), not 16x it (quadratic)."""
        for kind in ("content", "tool_args"):
            t_small, _ = self._time_accumulation(4_000, kind)
            t_big, _ = self._time_accumulation(16_000, kind)
            ratio = t_big / max(t_small, 1e-9)
            print(f"\n  {kind}: 4k chunks {t_small:.2f}s, "
                  f"16k chunks {t_big:.2f}s, ratio {ratio:.1f}x "
                  f"(linear ~= 4x, quadratic ~= 16x)")
            self.assertLess(ratio, 8.0,
                            f"{kind} accumulation looks quadratic "
                            f"(4x chunks -> {ratio:.1f}x time)")


def _selftest() -> int:
    """Standalone timing proof: prints per-token callback latency over a
    slow fake stream. Run with `python tests/test_stream_latency.py`."""
    print("== streaming incremental-delivery self-test ==")
    result, seen, start, end, tokens = _run_stream()
    stream_len = end - start
    print(f"stream of {N_TOKENS} tokens, {GAP * 1000:.0f}ms apart: "
          f"total {stream_len:.2f}s")
    print("token callback offsets (s):")
    for piece, ts in seen:
        print(f"  {piece.strip():>6}  +{ts - start:5.2f}s")
    first_delay = seen[0][1] - start
    span = seen[-1][1] - seen[0][1]
    ok = (len(seen) == N_TOKENS
          and result.content == "".join(tokens)
          and first_delay < stream_len * 0.6
          and span >= (N_TOKENS - 1) * GAP * 0.5)
    print(f"first token after {first_delay:.2f}s "
          f"(stream {stream_len:.2f}s), span {span:.2f}s -> "
          f"{'PASS: tokens delivered incrementally' if ok else 'FAIL: buffered'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_selftest())
