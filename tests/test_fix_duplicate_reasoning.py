"""kios refused history that echoed thinking under two keys.

Real-user log: two turns on Gemini 4 Argon produced reasoning, the user
switched (Ctrl+T) to Mistral Large 4 on kios, and the next turn failed:

    bad_response_status_code: Failed to deserialize the JSON body into the
    target type: duplicate field `reasoning_content` at line 1 column 21625

kios is a Rust/serde gateway where ``reasoning`` is an alias of
``reasoning_content``; our assistant history carried both. These tests run
the REAL client (and the real Agent) against a local mock gateway that
behaves like kios. No live provider, fake key only.
"""
import dataclasses
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp(prefix="dupreason_"))

import pytest  # noqa: E402

from fullagent import client, config  # noqa: E402
from fullagent.client import APIError  # noqa: E402
from fullagent.config import Model, Provider  # noqa: E402

KIOS_DUP_ERROR = {
    "error": {
        "message": ("Failed to deserialize the JSON body into the target "
                    "type: duplicate field `reasoning_content` at line 1 "
                    "column 21625"),
        "type": "bad_response_status_code",
    }
}

FAKE_KEY = "offline-test"
KIOS_MODEL = Model(id="mistral-large-4", provider="kios",
                   label="Mistral Large 4")


def _both_keys(message) -> bool:
    return (isinstance(message, dict) and "reasoning" in message
            and "reasoning_content" in message)


def _has_thinking(message) -> bool:
    return isinstance(message, dict) and bool(
        message.get("reasoning") or message.get("reasoning_content"))


class MockGateway:
    """A kios-like OpenAI-compatible gateway on 127.0.0.1:<random>.

    mode "serde"        : 400 duplicate-field when a message carries both
                          keys (serde alias), else a normal reply.
    mode "reject_once"  : additionally refuses ANY echoed thinking with the
                          same duplicate-field error, but only once.
    mode "always"       : refuses every request with the duplicate-field
                          error.
    """

    def __init__(self, mode: str = "serde", reasoning: str = "",
                 serde_models: set | None = None) -> None:
        self.mode = mode
        # models routed through the serde gateway (None: all). The Agent
        # test sends Gemini here too, which (like gwarden) took both keys.
        self.serde_models = serde_models
        self.reasoning = reasoning      # thinking to stream back
        # replies to serve in order; when empty, a plain "Hi there!" text.
        # A reply is {"tool_calls": [...]} for a tool-call turn.
        self.replies: list[dict] = []
        self.requests: list[dict] = []
        self.rejected = 0
        gw = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *_a):     # keep pytest output clean
                pass

            def do_POST(self):              # noqa: N802 — http.server API
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                gw.requests.append(body)
                if gw._reject(body):
                    gw.rejected += 1
                    raw = json.dumps(KIOS_DUP_ERROR).encode()
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                reply = gw.replies.pop(0) if gw.replies else {}
                if body.get("stream"):
                    self._stream(body, reply)
                else:
                    self._json(body, reply)

            def _stream(self, body, reply):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                chunks = []
                if gw.reasoning:
                    chunks.append({"reasoning_content": gw.reasoning})
                calls = reply.get("tool_calls")
                if calls:
                    chunks.append({"role": "assistant", "tool_calls": [
                        dict(c, index=i) for i, c in enumerate(calls)]})
                else:
                    chunks.append({"role": "assistant", "content": "Hi "})
                    chunks.append({"content": "there!"})
                for delta in chunks:
                    self._event({"id": "c1", "object": "chat.completion.chunk",
                                 "model": body.get("model"),
                                 "choices": [{"index": 0, "delta": delta,
                                              "finish_reason": None}]})
                self._event({"id": "c1", "object": "chat.completion.chunk",
                             "model": body.get("model"),
                             "choices": [{"index": 0, "delta": {},
                                          "finish_reason": ("tool_calls"
                                                            if calls
                                                            else "stop")}],
                             "usage": {"prompt_tokens": 50,
                                       "completion_tokens": 3,
                                       "total_tokens": 53}})
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

            def _event(self, obj):
                self.wfile.write(b"data: " + json.dumps(obj).encode()
                                 + b"\n\n")
                self.wfile.flush()

            def _json(self, body, reply):
                msg = {"role": "assistant", "content": "Hi there!"}
                if reply.get("tool_calls"):
                    msg = {"role": "assistant", "content": None,
                           "tool_calls": reply["tool_calls"]}
                if gw.reasoning:
                    msg["reasoning_content"] = gw.reasoning
                raw = json.dumps({
                    "id": "c1", "object": "chat.completion",
                    "model": body.get("model"),
                    "choices": [{"index": 0, "message": msg,
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 3,
                              "total_tokens": 53}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)

    def _reject(self, body: dict) -> bool:
        messages = body.get("messages") or []
        if self.mode == "always":
            return True
        serde = (self.serde_models is None
                 or body.get("model") in self.serde_models)
        if serde and any(_both_keys(m) for m in messages):
            return True                     # serde: alias given twice
        if self.mode == "reject_once" and self.rejected == 0:
            return any(_has_thinking(m) for m in messages)
        return False

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def provider(self, key: str = "kios") -> Provider:
        return dataclasses.replace(config.PROVIDERS[key],
                                   base_url=self.base_url, api_key=FAKE_KEY)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()

    def saw_both_keys(self) -> bool:
        return any(_both_keys(m) for req in self.requests
                   for m in req.get("messages") or [])


@pytest.fixture(autouse=True)
def local_only(monkeypatch):
    """The mock gateway is on loopback: never route it through a proxy,
    and never warm up a connection to a real provider."""
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setattr(client, "prewarm_connection", lambda _p: None)


def _low():
    return config.effort_by_key("low")


def _old_history() -> list[dict]:
    """History exactly as the pre-fix assistant_message() wrote it after
    two Gemini turns with thinking: every assistant turn has BOTH keys."""
    think1 = "User says hello. Likely wants greeting. Respond warmly."
    think2 = "Count files: run find . -type f | wc -l"
    return [
        {"role": "system", "content": "You are FullAgent."},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Hi there! How can I help?",
         "reasoning_content": think1, "reasoning": think1},
        {"role": "user", "content": "check akro yesme kitna fiels hai"},
        {"role": "assistant", "content": "(calling tools)",
         "reasoning_content": think2, "reasoning": think2,
         "tool_calls": [{"id": "call_1", "type": "function",
                         "function": {"name": "run_command",
                                      "arguments": json.dumps(
                                          {"command": "find . -type f | wc -l"})}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "60371"},
        {"role": "assistant", "content": "There are **60,371** files.",
         "reasoning_content": think2, "reasoning": think2},
        {"role": "user", "content": "build an advanced web scanning framework"},
    ]


def _new_history() -> list[dict]:
    """History in the fixed shape (reasoning_content only)."""
    msgs = _old_history()
    for m in msgs:
        m.pop("reasoning", None)
    return msgs


# --------------------------------------------------------------------------
# client: the request that failed in the log
# --------------------------------------------------------------------------

def test_stream_old_history_to_kios_succeeds_without_both_keys():
    with MockGateway("serde") as gw:
        msgs = _old_history()
        result = client.chat_stream(gw.provider(), KIOS_MODEL, _low(),
                                    msgs, None, timeout=20)
    assert result.content == "Hi there!"
    assert gw.requests, "the gateway saw no request"
    assert not gw.saw_both_keys(), "a message carried both thinking keys"
    assert gw.rejected == 0
    assert len(gw.requests) == 1
    # the thinking itself is kept, under the canonical key
    sent = gw.requests[0]["messages"]
    assert any(m.get("reasoning_content") for m in sent
               if m.get("role") == "assistant")


def test_blocking_old_history_to_kios_succeeds_without_both_keys():
    with MockGateway("serde") as gw:
        result = client.chat_blocking(gw.provider(), KIOS_MODEL, _low(),
                                      _old_history(), None, timeout=20)
    assert result.content == "Hi there!"
    assert gw.requests and not gw.saw_both_keys()
    assert gw.rejected == 0


def test_assistant_message_writes_one_thinking_key():
    msg = client.assistant_message("hi", [], "thought")
    assert msg["reasoning_content"] == "thought"
    assert "reasoning" not in msg


# --------------------------------------------------------------------------
# client: a gateway that refuses echoed thinking -> exactly one heal
# --------------------------------------------------------------------------

@pytest.mark.parametrize("send", ["stream", "blocking"])
def test_duplicate_field_rejection_heals_with_one_retry(send):
    with MockGateway("reject_once") as gw:
        msgs = _new_history()
        fn = client.chat_stream if send == "stream" else client.chat_blocking
        result = fn(gw.provider(), KIOS_MODEL, _low(), msgs, None,
                    timeout=20)
    assert result.content == "Hi there!"
    assert len(gw.requests) == 2, len(gw.requests)
    assert gw.rejected == 1
    assert any(_has_thinking(m) for m in gw.requests[0]["messages"])
    assert not any(_has_thinking(m) for m in gw.requests[1]["messages"])
    assert not gw.saw_both_keys()


@pytest.mark.parametrize("send", ["stream", "blocking"])
def test_gateway_that_always_rejects_retries_once_then_clear_error(send):
    with MockGateway("always") as gw:
        fn = client.chat_stream if send == "stream" else client.chat_blocking
        with pytest.raises(APIError) as info:
            fn(gw.provider(), KIOS_MODEL, _low(), _new_history(), None,
               timeout=20)
    assert len(gw.requests) == 2, len(gw.requests)   # original + one heal
    text = str(info.value)
    assert "duplicate field" in text
    assert "retried once" in text and "/new" in text
    assert info.value.status == 400


# --------------------------------------------------------------------------
# saved sessions written by the old code are cleaned on resume
# --------------------------------------------------------------------------

def test_resumed_old_session_has_one_thinking_key(tmp_path):
    from types import SimpleNamespace

    from fullagent import sessions

    sessions.set_store_dir(tmp_path)
    (tmp_path / "old12345.json").write_text(json.dumps(
        {"session_id": "old12345", "model_id": "gemini-4-argon",
         "messages": _old_history()}), encoding="utf-8")
    data = sessions.load("old12345")
    agent = SimpleNamespace(session_id="", messages=[])
    sessions.resume_into(agent, data)
    assert agent.messages
    assert not any(_both_keys(m) for m in agent.messages)
    kept = [m["reasoning_content"] for m in agent.messages
            if m.get("role") == "assistant"]
    assert all(kept), kept


# --------------------------------------------------------------------------
# the real Agent: two thinking turns on Gemini, Ctrl+T to kios, third turn
# --------------------------------------------------------------------------

@pytest.fixture()
def home(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(config, "EVENT_LOG_FILE",
                        tmp_path / "app" / "eventlog.jsonl")
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "app" / "sessions")
    monkeypatch.setattr(config, "CONFIG_FILE",
                        tmp_path / "app" / "config.json")
    config.ensure_dirs()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _run(ag, text):
    return ag.run_turn(text, lambda s: None, lambda s: None,
                       lambda e: None, lambda e: None, lambda s: None,
                       lambda t, a: True, should_cancel=lambda: False)


def test_agent_gemini_thinking_then_switch_to_kios(home, monkeypatch):
    import fullagent.agent as agent_mod

    gemini = config.model_by_id("gemini-4-argon")
    kios = config.model_by_id("mistral-large-4")
    assert gemini is not None and kios is not None
    assert kios.provider == "kios"

    with MockGateway("serde", reasoning="User says hello. Respond warmly.",
                     serde_models={kios.id}) as gw:
        for key in {gemini.provider, kios.provider}:
            monkeypatch.setitem(config.PROVIDERS, key, gw.provider(key))
        agent_mod._resolve_lazy(*agent_mod._LAZY_IMPORTS)
        if getattr(agent_mod, "PROVIDERS", None) is not config.PROVIDERS:
            for key in {gemini.provider, kios.provider}:
                monkeypatch.setitem(agent_mod.PROVIDERS, key,
                                    gw.provider(key))
        ag = agent_mod.Agent(config.Config())
        try:
            ag._ensure_session()
            ag.cfg.model_id = gemini.id
            ag.cfg.effort = "low"
            # turn 2 runs a tool, like the user's file count did; the
            # tool-call turn is the one whose thinking stays in history
            gw.replies = [{}, {"tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "list_dir",
                             "arguments": json.dumps({"path": "."})}}]}]
            for text in ("hello", "how many files are here"):
                turn = _run(ag, text)
                assert not turn.error, turn.error
                assert turn.model_id == gemini.id
            # the Gemini turns really produced thinking that is kept
            assert any(m.get("reasoning_content") for m in ag.messages
                       if isinstance(m, dict)
                       and m.get("role") == "assistant")
            ag.cfg.model_id = kios.id           # Ctrl+T -> Mistral Large 4
            gw.reasoning = ""
            before = len(gw.requests)
            turn = _run(ag, "build an advanced web scanning framework")
            assert not turn.error, turn.error
            assert turn.model_id == kios.id
            assert len(gw.requests) > before
            kios_reqs = [r for r in gw.requests[before:]
                         if r.get("model") == kios.id]
            assert kios_reqs, [r.get("model") for r in gw.requests]
            # the Gemini thinking really is echoed to kios (the trigger)
            assert any(m.get("reasoning_content") for r in kios_reqs
                       for m in r["messages"])
        finally:
            try:
                ag.subagents.shutdown(wait=False)
            except Exception:
                pass
    assert gw.rejected == 0
    assert not gw.saw_both_keys()
