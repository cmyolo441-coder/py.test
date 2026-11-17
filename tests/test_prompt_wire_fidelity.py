"""A ~49k-char system prompt must reach the provider byte-identical as
messages[0] (role "system") on EVERY call: first attempt, HTTP retries,
tool-loop iterations (with prune + budget capping), blocking fallback and
provider/model fallbacks. Offline: a fake HTTP session captures the JSON
body exactly as it would be serialised onto the wire."""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fullagent import client
from fullagent.agent import prune_messages
from fullagent.config import PROVIDERS, EFFORTS, Model, Provider
from fullagent.promptbudget import bound_prompt

# non-ASCII + quotes + newlines + a literal "system" word, ~49k chars
_UNIT = ("RULE: always follow this \"system\" prompt.\nnaïve – café ✓ 日本語 \\ \t"
         "{json: [1,2]} <tag>&amp;\n")
SYSTEM = (_UNIT * (49_000 // len(_UNIT) + 1))[:49_000]
assert len(SYSTEM) == 49_000


def _sse(obj) -> bytes:
    return b"data: " + json.dumps(obj).encode() + b"\n"


class FakeResp:
    def __init__(self, status=200, events=None, body="", stream=True):
        self.status_code = status
        self.headers = {}
        self._events = events or []
        self._body = body
        self.text = body
        self.raw = None

    def iter_lines(self):
        for e in self._events:
            yield _sse(e).rstrip(b"\n")
            yield b""
        yield b"data: [DONE]"
        yield b""

    def json(self):
        return json.loads(self._body)

    @property
    def content(self):
        return self._body.encode()

    def close(self):
        pass


def _ok_stream(text="ok", tool=None):
    delta = {"content": text}
    if tool:
        delta = {"tool_calls": [{"index": 0, "id": "c%d" % tool[0],
                                 "function": {"name": "t", "arguments": "{}"}}]}
    return FakeResp(events=[{"choices": [{"delta": delta}]},
                            {"choices": [{"delta": {}, "finish_reason": "stop"}]}])


def _ok_blocking(text="ok"):
    return FakeResp(body=json.dumps({"choices": [{"message": {
        "role": "assistant", "content": text}, "finish_reason": "stop"}]}))


class FakeSession:
    """Records the exact wire bytes of each POST (json= is what requests
    would serialise)."""

    def __init__(self, script):
        self.script = list(script)
        self.bodies: list[dict] = []
        self.urls: list[str] = []

    def post(self, url, headers=None, json=None, **kw):  # noqa: A002
        import json as _j
        self.urls.append(url)
        self.bodies.append(_j.loads(_j.dumps(json)))   # snapshot at send time
        item = self.script.pop(0) if self.script else _ok_stream()
        return item() if callable(item) else item

    def get(self, *a, **k):
        return FakeResp()


def _model(pid="kios", mid="mistral-large-4", window=262_144, **kw):
    return Model(mid, pid, mid, supports_tools=True, context_window=window, **kw)


def _provider(pid="kios"):
    p = PROVIDERS.get(pid)
    return Provider(pid, pid, "http://fake/v1", "k", "white")


def _assert_first_is_system(tc: unittest.TestCase, bodies):
    tc.assertTrue(bodies)
    for b in bodies:
        m0 = b["messages"][0]
        tc.assertEqual(m0["role"], "system")
        tc.assertEqual(m0["content"], SYSTEM)       # byte-identical
        tc.assertEqual(sum(m["role"] == "system" for m in b["messages"][:1]), 1)


def _conv(n_turns=0, big_tool=0):
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": "do the thing"}]
    for i in range(n_turns):
        cid = f"call_{i}"
        msgs.append({"role": "assistant", "content": "x", "tool_calls": [{
            "id": cid, "type": "function",
            "function": {"name": "t", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": cid,
                     "content": "r" * (big_tool or 50)})
    return msgs


@mock.patch.object(client, "_backoff", lambda attempt: 0.0)
class WireFidelity(unittest.TestCase):
    def setUp(self):
        # isolate from process-global calibration learned by other tests
        for name, val in (("_correction_factor", lambda *a, **k: 1.0),
                          ("effective_window", lambda m: m.context_window)):
            p = mock.patch.object(client, name, val)
            p.start()
            self.addCleanup(p.stop)

    def _run(self, sess, fn):
        with mock.patch.object(client, "_http", return_value=sess):
            return fn()

    def test_stream_first_call(self):
        s = FakeSession([_ok_stream()])
        msgs = _conv()
        self._run(s, lambda: client.chat_stream(
            _provider(), _model(), EFFORTS[2], msgs, None))
        _assert_first_is_system(self, s.bodies)

    def test_stream_retries_after_503_and_429(self):
        s = FakeSession([FakeResp(503, body="{}"), FakeResp(502, body="{}"),
                         _ok_stream()])
        self._run(s, lambda: client.chat_stream(
            _provider(), _model(), EFFORTS[2], _conv(), None))
        self.assertEqual(len(s.bodies), 3)
        _assert_first_is_system(self, s.bodies)
        self.assertEqual(s.bodies[0], s.bodies[2])

    def test_blocking_and_blocking_retry(self):
        s = FakeSession([FakeResp(500, body="{}"), _ok_blocking()])
        self._run(s, lambda: client.chat_blocking(
            _provider(), _model(), EFFORTS[2], _conv(), None))
        self.assertEqual(len(s.bodies), 2)
        _assert_first_is_system(self, s.bodies)

    def test_tool_loop_with_prune_and_budget_every_iteration(self):
        """Mirror Agent._prune_for_model: prune_messages + bound_prompt each
        iteration, history growing with fat tool outputs, tiny budget."""
        s = FakeSession([])
        history = _conv()
        prov, mod = _provider(), _model()
        for i in range(30):
            view = prune_messages(history, window=6)
            view = bound_prompt(view, budget_tokens=14_000, model_id=mod.id)
            self.assertEqual(view[0]["content"], SYSTEM)
            self._run(s, lambda v=view: client.chat_stream(
                prov, mod, EFFORTS[2], v, [{"type": "function", "function": {
                    "name": "t", "parameters": {"type": "object"}}}]))
            cid = f"loop_{i}"
            history.append({"role": "assistant", "content": "c", "tool_calls": [{
                "id": cid, "type": "function",
                "function": {"name": "t", "arguments": "{}"}}]})
            history.append({"role": "tool", "tool_call_id": cid,
                            "content": "T" * 20_000})
        self.assertEqual(len(s.bodies), 30)
        _assert_first_is_system(self, s.bodies)
        # canonical history was never altered
        self.assertEqual(history[0]["content"], SYSTEM)

    def test_window_prune_in_build_payload_keeps_system(self):
        """Window pre-check prunes history, never the system prompt."""
        s = FakeSession([])
        mod = _model(window=40_000)   # 49k chars ~ 12k tokens; history won't fit
        msgs = _conv(n_turns=40, big_tool=4_000)
        before = len(msgs)
        self._run(s, lambda: client.chat_stream(
            _provider(), mod, EFFORTS[2], msgs, None))
        _assert_first_is_system(self, s.bodies)
        self.assertLess(len(s.bodies[0]["messages"]), before)

    def test_irreducible_core_keeps_system(self):
        mod = _model(window=32_000)
        msgs = _conv(n_turns=10, big_tool=30_000)
        with mock.patch.object(client, "_prune_messages_view",
                               side_effect=lambda m, window: list(m)):
            p = client.build_payload(mod, EFFORTS[2], msgs, None)
        self.assertEqual(p["messages"][0], {"role": "system", "content": SYSTEM})

    def test_repair_and_sanitize_leave_system_untouched(self):
        msgs = _conv(3)
        msgs.insert(3, {"role": "assistant", "content": "dup"})   # merge case
        msgs.append({"role": "tool", "tool_call_id": "orphan", "content": "z"})
        p = client.build_payload(_model(), EFFORTS[2], msgs, None)
        self.assertEqual(p["messages"][0], {"role": "system", "content": SYSTEM})
        self.assertEqual(sum(m["role"] == "system" for m in p["messages"]), 1)

    def test_free_models_and_provider_fallback_chain(self):
        """Every configured model (kios/naga/gwarden/free...) -> same bytes;
        and a fallback from one provider to another resends the same prompt."""
        from fullagent.config import MODELS
        s = FakeSession([])
        for m in MODELS:
            prov = _provider(m.provider)
            msgs = _conv()
            if m.provider == "devin" or "devin" in m.provider:
                continue
            self._run(s, lambda: client.chat_stream(prov, m, EFFORTS[2], msgs, None))
        self.assertGreaterEqual(len(s.bodies), 3)
        _assert_first_is_system(self, s.bodies)
        # fallback: provider A hard-fails (400 non-retryable) then B succeeds
        msgs = _conv()
        s2 = FakeSession([FakeResp(401, body="{}"), _ok_stream()])
        def go():
            try:
                client.chat_stream(_provider("kios"), _model(), EFFORTS[2], msgs, None)
            except client.APIError:
                client.chat_stream(_provider("naga"), _model("naga", "x/y"),
                                   EFFORTS[2], msgs, None)
        self._run(s2, go)
        self.assertEqual(len(s2.bodies), 2)
        _assert_first_is_system(self, s2.bodies)

    def test_stream_to_blocking_degrade(self):
        s = FakeSession([FakeResp(400, body='{"error":{"message":"stream unsupported"}}'),
                         _ok_blocking()])
        msgs = _conv()
        def go():
            try:
                client.chat_stream(_provider(), _model(), EFFORTS[2], msgs, None)
            except client.APIError:
                client.chat_blocking(_provider(), _model(), EFFORTS[2], msgs, None)
        self._run(s, go)
        _assert_first_is_system(self, s.bodies)

    def test_overflow_reclamp_retry_resends_same_prompt(self):
        s = FakeSession([FakeResp(400, body=json.dumps({"error": {"message":
            "This model's maximum context length is 262144 tokens. However, you "
            "requested 300000 tokens (100000 tokens from the input and 200000 tokens "
            "for completion)"}})), _ok_stream()])
        with mock.patch.object(client, "_save_calibration", lambda: None), \
                mock.patch.object(client, "learn_token_correction",
                                  lambda *a, **k: None), \
                mock.patch.object(client, "learn_context_window",
                                  lambda *a, **k: None):
            self._run(s, lambda: client.chat_stream(
                _provider(), _model(), EFFORTS[2], _conv(), None))
        self.assertEqual(len(s.bodies), 2)
        _assert_first_is_system(self, s.bodies)

    def test_wire_bytes_identical_json(self):
        """json.dumps of the posted body round-trips the prompt exactly."""
        s = FakeSession([_ok_stream()])
        self._run(s, lambda: client.chat_stream(
            _provider(), _model(), EFFORTS[2], _conv(), None))
        raw = json.dumps(s.bodies[0], ensure_ascii=False)
        self.assertEqual(json.loads(raw)["messages"][0]["content"].encode(),
                         SYSTEM.encode())


if __name__ == "__main__":
    unittest.main()
